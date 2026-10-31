"""Opt-in, in-process B1 assembly. Importing this module grants nothing.

No CLI, plugin loader, credential discovery, queue or authority database. The
caller supplies external grants, bounded Git capabilities (including #217 CAS
and the pinned Source Guard), and fixture identities independently of a plan.
Recovery files are create-once evidence, NEVER authorization. Cooperative
single-host use only; an unresolved attempt is not automatically retried.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable, Mapping

if __package__:
    from .kagari_vnext_main_service import MainHostCapabilities
    from .kagari_vnext_main import InitialCollaboration
else:
    from kagari_vnext_main_service import MainHostCapabilities
    from kagari_vnext_main import InitialCollaboration

from collaboration_git import TaskGit, BranchPush
from issue_worktree import IssueWorktreeAllocator
from metadata_ref import MetadataStore
from task_record import TaskRecords, validate_branch_ref
from task_collaboration import (
    TaskCollaboration, WriteIntent, CreateDraftIntent, EditMetadataIntent,
    PostCommentIntent, RepositoryFacts, IssueFacts, PullFacts, CommentFacts,
    MetadataBinding,
)
from task_view import TaskViewSnapshots, SNAPSHOT_KIND


class TrustedHostError(ValueError):
    """Secret-free blocker, including ambiguous outcomes requiring recovery."""


def _require(condition, code='host_binding_invalid'):
    if not condition:
        raise TrustedHostError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    try:
        _require(type(data) is bytes and len(data) <= 1024 * 1024, 'api_size_limit')
        return json.loads(data.decode('utf-8'), object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise TrustedHostError('invalid_json') from None


def _positive(n):
    _require(type(n) is int and 0 < n <= 10**12)
    return n


def _text(s, maximum=65536):
    _require(type(s) is str and len(s.encode('utf-8')) <= maximum
             and not any(ord(c) < 32 and c not in '\n\t' for c in s)
             and '\x7f' not in s)
    return s


def _body(s):
    # GitHub permits null bodies, not arbitrary false-y JSON values.
    return _text('' if s is None else s)


def _oid(s):
    _require(type(s) is str and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', s))
    return s


def _key(value):
    return hashlib.sha256(b'kagari-b1-recovery/v1\n' + _canonical(value)).hexdigest()


class RecoveryJournal:
    """Bounded create-once candidate/attempt evidence in a host-owned directory.

    The trusted caller creates the private directory. No lock, replacement,
    deletion or automatic repair is provided. Partial files block recovery.
    fsync of both file and directory precedes any publication callback return.
    """
    def __init__(self, directory: Path):
        self.directory = directory
        self._identity = self._check()

    def _check(self):
        try:
            p = self.directory
            s = p.lstat()
            _require(p.is_absolute() and p.resolve(strict=True) == p
                     and stat.S_ISDIR(s.st_mode) and s.st_uid == os.geteuid()
                     and not s.st_mode & 0o077, 'recovery_directory_unsafe')
            identity = (s.st_dev, s.st_ino)
            _require(not hasattr(self, '_identity') or identity == self._identity,
                     'recovery_directory_changed')
            return identity
        except OSError:
            raise TrustedHostError('recovery_directory_unsafe') from None

    def _open_dir(self):
        self._check()
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        s = os.fstat(fd)
        if (s.st_dev, s.st_ino) != self._identity:
            os.close(fd)
            raise TrustedHostError('recovery_directory_changed')
        return fd

    def read(self, key):
        _require(type(key) is str and re.fullmatch('[0-9a-f]{64}', key))
        directory = self._open_dir()
        try:
            try:
                fd = os.open(key, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, 'rb') as f:
                s = os.fstat(f.fileno())
                _require(stat.S_ISREG(s.st_mode) and s.st_uid == os.geteuid()
                         and s.st_nlink == 1 and not s.st_mode & 0o077
                         and s.st_size <= 16384, 'recovery_record_unsafe')
                data = f.read(16385)
            result = _json(data)
            _require(type(result) is dict and _canonical(result) == data,
                     'recovery_record_invalid')
            return result
        except OSError:
            raise TrustedHostError('recovery_record_unsafe') from None
        finally:
            os.close(directory)

    def create(self, key, record):
        _require(type(key) is str and re.fullmatch('[0-9a-f]{64}', key))
        data = _canonical(record)
        _require(type(record) is dict and len(data) <= 16384)
        directory = self._open_dir()
        try:
            fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.fsync(directory)
        except OSError:
            raise TrustedHostError('recovery_required_no_retry') from None
        finally:
            os.close(directory)


class GitHubHTTPS:
    """Host-private bounded REST executor; token is explicitly supplied by host.

    No redirects, proxy/env lookup, retry, pagination or arbitrary hostname.
    Give this callable ONLY to BoundedGitHubTransport/assemble_host, never a
    Task. Possession of this credential-bearing executor is a host capability;
    it is NOT an agent-facing generic API or a substitute for #194 approval.
    Byte limits and socket timeout apply. Never log token/responses.
    """
    def __init__(self, repository: str, token: str):
        _require(type(repository) is str and re.fullmatch(
            r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repository)
            and all(p not in {'.', '..'} for p in repository.split('/')))
        self._prefix = '/repos/' + repository
        _require(type(token) is str and 1 <= len(token) <= 4096
                 and all(33 <= ord(c) <= 126 for c in token), 'credential_required')
        self._token = token

    def __call__(self, method, path, body):
        suffix = path[len(self._prefix):] if path.startswith(self._prefix) else None
        allowed = (method == 'GET' and body is None and (
            path == '/user' or suffix == '' or (suffix is not None and re.fullmatch(
                r'/(?:issues/[1-9][0-9]*|pulls/[1-9][0-9]*|'
                r'pulls\?state=all&sort=created&direction=asc&per_page=100&page=(?:[1-9][0-9]?|100)|'
                r'issues/[1-9][0-9]*/comments\?per_page=100&page=(?:[1-9][0-9]?|100))', suffix))
        )) or (method == 'POST' and type(body) is bytes and len(body) <= 65536
               and suffix is not None and re.fullmatch(
                   r'/pulls|/issues/[1-9][0-9]*/comments', suffix))
        _require(allowed, 'endpoint_denied')
        connection = http.client.HTTPSConnection('api.github.com', timeout=15)
        try:
            connection.request(method, path, body=body, headers={
                'Authorization': 'Bearer ' + self._token,
                'Accept': 'application/vnd.github+json',
                'X-GitHub-Api-Version': '2022-11-28',
                'User-Agent': 'KUMIKI-B1-trusted-host',
                'Content-Type': 'application/json',
            })
            response = connection.getresponse()
            _require(response.status in ({200} if method == 'GET' else {200, 201}),
                     'github_http_unconfirmed')
            _require(response.getheader('Content-Type', '').split(';')[0]
                     == 'application/json', 'github_content_type')
            data = response.read(1024 * 1024 + 1)
            _require(len(data) <= 1024 * 1024, 'api_size_limit')
            return data
        except Exception:
            raise TrustedHostError('github_http_unconfirmed') from None
        finally:
            connection.close()


class BoundedGitHubTransport:
    """One repo/Issue/branch; fixed REST endpoints, typed one-use write permits.

    REST's documented extra fields are ignored, never promoted to facts or
    authority. Missing/ill-typed consumed fields and duplicate JSON keys fail.
    #194 reobserves all pages twice and verifies exact postconditions.
    """
    def __init__(self, *, repository, repository_id, principal_id, issue,
                 branch_ref, default_branch_ref, execute, consume, journal):
        _require(type(repository) is str and re.fullmatch(
            r'[A-Za-z0-9-]+/[A-Za-z0-9_.-]+', repository))
        _require(all(part not in {'.', '..'} for part in repository.split('/')))
        validate_branch_ref(branch_ref)
        validate_branch_ref(default_branch_ref)
        _require(branch_ref != default_branch_ref and callable(execute)
                 and callable(consume) and type(journal) is RecoveryJournal)
        self.repo = repository
        self.repo_id = _positive(repository_id)
        self.actor = _positive(principal_id)
        self.number = _positive(issue)
        self.branch = branch_ref
        self.default = default_branch_ref
        self._execute, self._consume, self._journal = execute, consume, journal
        self._prefix = '/repos/' + repository

    def _get(self, suffix):
        return _json(self._execute('GET', self._prefix + suffix, None))

    def repository(self):
        d = self._get('')
        _require(type(d) is dict and d['full_name'] == self.repo
                 and _positive(d['id']) == self.repo_id
                 and 'refs/heads/' + d['default_branch'] == self.default)
        return RepositoryFacts(self.repo, self.default, self.repo_id)

    def principal(self):
        d = _json(self._execute('GET', '/user', None))
        _require(type(d) is dict and _positive(d['id']) == self.actor)
        _text(d['login'], 128)
        return self.actor

    def _pull(self, d):
        _require(type(d) is dict and d['base']['repo']['full_name'] == self.repo
                 and _positive(d['base']['repo']['id']) == self.repo_id)
        head_repo = d['head']['repo']
        head_name = None if head_repo is None else head_repo['full_name']
        if head_name == self.repo:
            _require(_positive(head_repo['id']) == self.repo_id)
        state = d['state']
        _require(state in {'open', 'closed'} and type(d['draft']) is bool)
        if d.get('merged_at') is not None:
            _require(state == 'closed' and type(d['merged_at']) is str)
            state = 'merged'
        base, head = d['base']['ref'], d['head']['ref']
        validate_branch_ref('refs/heads/' + base)
        validate_branch_ref('refs/heads/' + head)
        return PullFacts(_positive(d['number']), self.repo, head_name, base, head,
                         _oid(d['head']['sha']), state, d['draft'],
                         _text(d['title'], 1024), _body(d['body']))

    def _page(self, page):
        _require(type(page) is int and 1 <= page <= 100, 'github_page_limit')
        return f'per_page=100&page={page}'

    def pull_page(self, page):
        d = self._get('/pulls?state=all&sort=created&direction=asc&' + self._page(page))
        _require(type(d) is list and len(d) <= 100, 'github_page_limit')
        return tuple(self._pull(item) for item in d)

    def issue(self, number):
        _require(type(number) is int and number == self.number)
        d = self._get(f'/issues/{number}')
        _require(type(d) is dict and 'pull_request' not in d
                 and _positive(d['number']) == number
                 and d['repository_url'] == 'https://api.github.com' + self._prefix
                 and d['state'] in {'open', 'closed'})
        return IssueFacts(self.repo, number, _text(d['title'], 1024),
                          _body(d['body']), d['state'])

    def _target(self, kind, number):
        _positive(number)
        _require(kind in {'issue', 'pr'})
        if kind == 'issue':
            _require(number == self.number)
            self.issue(number)
        else:
            self._exact_pull(number)

    def _exact_pull(self, number, subject=None):
        p = self._pull(self._get(f'/pulls/{_positive(number)}'))
        _require(p.number == number and p.head_repository == self.repo
                 and 'refs/heads/' + p.head_ref == self.branch
                 and 'refs/heads/' + p.base_ref == self.default
                 and p.state == 'open' and p.draft
                 and (subject is None or p.head_oid == subject))
        return p

    def _comment(self, d, kind, number):
        _require(type(d) is dict and d['issue_url'] == (
            'https://api.github.com' + self._prefix + f'/issues/{number}'))
        return CommentFacts(self.repo, kind, number, _positive(d['id']),
                            _positive(d['user']['id']), _text(d['body']))

    def comments_page(self, kind, number, page):
        query = self._page(page)
        self._target(kind, number)
        d = self._get(f'/issues/{number}/comments?' + query)
        _require(type(d) is list and len(d) <= 100, 'github_page_limit')
        return tuple(self._comment(item, kind, number) for item in d)

    def _write(self, typed, operation, params, suffix, method, payload, identity):
        w = typed.intent
        _require(type(w) is WriteIntent and w.operation in operation
                 and w.repository == self.repo and w.task == str(self.number)
                 and w.branch_ref == self.branch and w.principal == self.actor)
        _oid(w.subject)
        decoded = _json(w.parameters)
        _require(decoded['operation_parameters'] == params, 'intent_payload_mismatch')
        content = (_canonical({'title': typed.title, 'body': typed.body})
                   if type(typed) is CreateDraftIntent else typed.body.encode('utf-8'))
        _require(self._consume(w, content) is True, 'unapproved_write_intent')
        # Stable identity excludes view_id/metadata tip; changing a report or
        # reconnecting cannot turn an uncertain attempt into a new permission.
        key = _key(['github-attempt', self.repo, self.number, self.branch, identity])
        self._journal.create(key, {'intent_id': w.intent_id,
                                  'subject': w.subject, 'record_id': w.record_id,
                                  'contract_id': w.contract_id,
                                  'payload_sha256': hashlib.sha256(_canonical(payload)).hexdigest()})
        return _json(self._execute(method, self._prefix + suffix, _canonical(payload)))

    def create_draft(self, i):
        _require(type(i) is CreateDraftIntent and i.repository == self.repo
                 and i.task == str(self.number) and i.branch_ref == self.branch
                 and i.base_ref == self.default and i.head_oid == i.intent.subject)
        _text(i.title, 240)
        _text(i.body, 16384)
        p = {'base_ref': i.base_ref, 'body': i.body, 'draft': True,
             'head_oid': i.head_oid, 'head_ref': i.branch_ref, 'title': i.title}
        result = self._write(i, {'collaboration.draft_pr'}, p, '/pulls', 'POST',
                            {'base': self.default[11:], 'head': self.branch[11:],
                             'title': i.title, 'body': i.body, 'draft': True}, 'draft')
        pull = self._pull(result)
        _require(pull.head_repository == self.repo and pull.head_oid == i.head_oid
                 and pull.head_ref == self.branch[11:] and pull.base_ref == self.default[11:]
                 and pull.state == 'open' and pull.draft
                 and (pull.title, pull.body) == (i.title, i.body))

    def edit_metadata(self, i):
        # GitHub PATCH has no field CAS. B1 deliberately refuses this operation
        # rather than presenting a pre-read as atomic exclusion of other editors.
        _require(type(i) is EditMetadataIntent, 'typed_intent_required')
        raise TrustedHostError('metadata_edit_requires_external_exclusion')

    def post_comment(self, i):
        _require(type(i) is PostCommentIntent and i.repository == self.repo)
        self._target(i.kind, i.number)
        if i.kind == 'pr':
            self._exact_pull(i.number, i.intent.subject)
        _text(i.body, 32768)
        params = _json(i.intent.parameters)['operation_parameters']
        # Checkpoints bind snapshot/report digest, not rendered body. The
        # one-use permit additionally binds the exact approved ContentPolicy bytes.
        _require(params.get('number', params.get('pr_number')) == i.number)
        if 'kind' in params:
            _require(params['kind'] == i.kind)
        result = self._write(i, {'collaboration.initial_checkpoint',
            'collaboration.turn_checkpoint', 'collaboration.factual_comment'},
            params, f'/issues/{i.number}/comments', 'POST', {'body': i.body},
            ['comment', i.kind, i.number, params.get('key', 'factual')])
        c = self._comment(result, i.kind, i.number)
        _require(c.author_id == self.actor and c.body == i.body)


@dataclass(frozen=True)
class HostTask:
    issue: int
    branch_ref: str
    base_revision: str
    record_id: str
    contract_id: str


def assemble_host(*, primary_store: MetadataStore, tasks: Mapping[int, HostTask],
                  repository_id: int, principal_id: int, default_branch_ref: str,
                  execute: Callable, store_for_worktree: Callable,
                  publish_branch: Callable, prerequisites_by_operation: Mapping,
                  authorize: Callable, record_intent: Callable,
                  content_policy: Callable, authorize_snapshot: Callable,
                  journal: RecoveryJournal, report: str) -> MainHostCapabilities:
    """Install explicit trusted capabilities, never infer grants from selections.

    store_for_worktree must install the real #217 direct-ref CAS. publish_branch
    must enforce the pinned Source Guard and ordinary non-force exact publication.
    No implementation here weakens either externally installed boundary.
    """
    _require(isinstance(primary_store, MetadataStore) and type(journal) is RecoveryJournal)
    _require(all(callable(c) for c in (execute, store_for_worktree, publish_branch,
        authorize, record_intent, content_policy, authorize_snapshot)), 'host_callbacks_required')
    _require(isinstance(prerequisites_by_operation, Mapping)
             and {'collaboration.bootstrap', 'collaboration.push',
                  'collaboration.draft_pr', 'collaboration.initial_checkpoint'}
             <= prerequisites_by_operation.keys(), 'prerequisites_required')
    selected = dict(tasks)
    _require(bool(selected))
    for number, task in selected.items():
        _positive(number)
        _require(type(task) is HostTask and task.issue == number)
        validate_branch_ref(task.branch_ref)
        _oid(task.base_revision)
        _require(all(type(v) is str and re.fullmatch('[0-9a-f]{64}', v)
                     for v in (task.record_id, task.contract_id)))

    def transport(choice, consume):
        return BoundedGitHubTransport(repository=primary_store.repository,
            repository_id=repository_id, principal_id=principal_id, issue=choice.issue,
            branch_ref=choice.branch_ref, default_branch_ref=default_branch_ref,
            execute=execute, consume=consume, journal=journal)

    def read_issue(number):
        _require(type(number) is int and number in selected)
        gh = transport(selected[number], lambda _: False)
        gh.repository()
        d = gh.issue(number)
        return {'repository': d.repository, 'number': d.number, 'title': d.title,
                'body': d.body, 'state': d.state.upper()}

    def install(number, worktree):
        _require(type(number) is int and number in selected)
        choice = selected[number]
        allocator = IssueWorktreeAllocator(primary_store)
        expected = primary_store.root / '.worktrees' / str(number)
        _require(worktree == expected and worktree.resolve(strict=True) == expected)
        allocator._registered(worktree, choice.branch_ref)
        store = store_for_worktree(worktree)
        _require(isinstance(store, MetadataStore) and store.root == worktree
                 and store.repository == primary_store.repository
                 and callable(store._direct_ref_cas), 'metadata_cas_required')
        tip = store.fetch_tip()
        current = TaskRecords(store, authorize=lambda _: False).read(
            tip, task=str(number), branch_ref=choice.branch_ref,
            base_revision=choice.base_revision)
        _require(current is not None and current[0] == choice.record_id
                 and current[1]['payload']['contract_id'] == choice.contract_id)
        permits = {}
        authorized = set()
        accepted_content = {}

        def decide(request):
            allowed = authorize(request) is True
            if allowed:
                authorized.add(request.intent_id)
            return allowed

        def record(intent):
            _require(type(intent) is WriteIntent and intent.intent_id in authorized
                     and intent.record_id == choice.record_id
                     and intent.contract_id == choice.contract_id)
            authorized.remove(intent.intent_id)
            if record_intent(intent) is not True:
                return False
            permits[intent.intent_id] = (intent, accepted_content.pop(intent.intent_id, None))
            return True

        def content(request):
            allowed = content_policy(request) is True
            if allowed:
                accepted_content[request.intent_id] = request.content
            return allowed

        def consume(intent, content=None):
            _require(permits.pop(intent.intent_id, None) == (intent, content),
                     'unapproved_write_intent')
            return True

        def push(intent):
            _require(type(intent) is BranchPush and intent.repository == store.repository
                     and intent.task == str(number) and intent.branch_ref == choice.branch_ref)
            matches = [w for w, _ in permits.values() if w.operation == 'collaboration.push'
                       and w.subject == intent.subject
                       and _json(w.parameters)['expected_remote'] == intent.expected_remote]
            _require(len(matches) == 1, 'unapproved_branch_publication')
            consume(matches[0])
            return publish_branch(intent)

        github = transport(choice, consume)
        git = TaskGit(store, task=str(number), branch_ref=choice.branch_ref,
                      base_revision=choice.base_revision,
                      default_branch_ref=default_branch_ref, publish=push)
        facade = TaskCollaboration(store, git, github,
            prerequisites_by_operation=dict(prerequisites_by_operation),
            authorize=decide, record_intent=record, content_policy=content)

        def metadata(repository, task, branch, pr, subject):
            _require((repository, task, branch) == (store.repository, str(number), choice.branch_ref))
            github._exact_pull(pr, subject)
            identity = [repository, task, branch, pr, subject,
                        choice.record_id, choice.contract_id]
            key = _key(['initial-explicit-handoff', identity])
            prior = journal.read(key)
            snapshots = TaskViewSnapshots(store, authorize=authorize_snapshot)

            def confirm(record):
                _require(record.keys() == {'identity', 'candidate', 'snapshot_id'}
                         and record['identity'] == identity, 'snapshot_recovery_conflict')
                candidate, sid = record['candidate'], record['snapshot_id']
                try:
                    store.confirm(candidate, required_objects=((SNAPSHOT_KIND, sid, task, subject),))
                    snapshot = snapshots.read(candidate, sid, task=task, subject=subject)
                    view = snapshot['view']
                    _require(snapshot['boundary'] == 'explicit-handoff'
                             and view['branch_ref'] == branch
                             and view['authority']['record_id'] == choice.record_id
                             and view['authority']['contract_id'] == choice.contract_id
                             and view['github']['pull_request']['number'] == pr
                             and view['github']['pull_request']['head_oid'] == subject
                             and view['authority']['base_revision'] == choice.base_revision)
                except Exception:
                    raise TrustedHostError('snapshot_recovery_required_no_recapture') from None
                return MetadataBinding(candidate, choice.record_id, choice.contract_id, sid)

            if prior is not None:
                return confirm(prior)
            captured_id = []

            def approve(request):
                _require((request.repository, request.task, request.subject,
                          request.record_id, request.contract_id, request.boundary) ==
                         (repository, task, subject, choice.record_id,
                          choice.contract_id, 'explicit-handoff'))
                if authorize_snapshot(request) is not True:
                    return False
                captured_id.append(request.snapshot_id)
                return True

            def candidate(oid):
                _require(len(captured_id) == 1)
                journal.create(key, {'identity': identity, 'candidate': oid,
                                     'snapshot_id': captured_id[0]})

            publication = TaskViewSnapshots(store, authorize=approve).capture(
                task=task, branch_ref=branch, base_revision=choice.base_revision,
                boundary='explicit-handoff', default_branch_ref=default_branch_ref,
                observe_remote_head=True, pr_number=pr,
                github_reader=facade.github_observation, on_candidate=candidate)
            # MetadataStore may find the exact object already present (no CAS).
            # Record that exact confirmed binding; never substitute fetch_tip.
            if journal.read(key) is None:
                journal.create(key, {'identity': identity,
                    'candidate': publication.metadata_commit,
                    'snapshot_id': publication.snapshot_id})
            return confirm(journal.read(key))

        return InitialCollaboration(facade, metadata, report)

    return MainHostCapabilities(read_issue, install)
