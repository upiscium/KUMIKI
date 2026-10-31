"""B1 synthetic host tests. No credentials, live GitHub or model dispatch."""
from pathlib import Path
import copy
from dataclasses import replace
import json
import tempfile
import unittest
from unittest.mock import patch

from tools import kagari_vnext_trusted_host as host
from tools.kagari_vnext_trusted_host import RecoveryJournal, TrustedHostError
from tests import test_kagari_vnext_main as fixtures
from tests import test_metadata_ref as git_fixture
from issue_worktree import IssueWorktreeAllocator
import operation_prerequisites as prerequisites
from task_collaboration import OPERATIONS, TaskCollaborationError
from task_view import TaskViewSnapshots


class JournalTests(unittest.TestCase):
    def test_missing_journal_fails_closed(self):
        with self.assertRaises(TrustedHostError):
            RecoveryJournal(Path('/nonexistent/b1-journal'))

    def test_create_once_reconnect_and_partial_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            key = 'a' * 64
            journal = RecoveryJournal(p)
            journal.create(key, {'candidate': 'x'})
            self.assertEqual(RecoveryJournal(p).read(key), {'candidate': 'x'})
            with self.assertRaises(TrustedHostError):
                journal.create(key, {'candidate': 'y'})
            (p / ('b' * 64)).write_bytes(b'{')
            with self.assertRaises(TrustedHostError):
                journal.read('b' * 64)

    def test_symlink_and_extra_record_data_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / ('a' * 64)).symlink_to(p / 'target')
            with self.assertRaises(TrustedHostError):
                RecoveryJournal(p).read('a' * 64)


class FakeREST:
    """Only fixed fake REST paths; never shells out or opens a connection."""
    prefix = '/repos/acme/widgets'

    def __init__(self, facts):
        self.facts = facts
        self.pulls = []
        self.comments = []
        self.calls = []
        self.posts = []
        self.lose_ack = set()
        self.drop = set()
        self.mutate = None

    def pull(self, payload):
        return {'number': 91, 'base': {'repo': {'full_name': 'acme/widgets', 'id': 7},
                    'ref': payload['base']},
                'head': {'repo': {'full_name': 'acme/widgets', 'id': 7},
                         'ref': payload['head'], 'sha': self.subject},
                'state': 'open', 'draft': True, 'title': payload['title'],
                'body': payload['body'], 'merged_at': None}

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        if method == 'POST':
            self.posts.append(path)
            payload = json.loads(body)
            if path.endswith('/pulls'):
                result = self.pull(payload)
                target = self.pulls
            elif path.endswith('/issues/91/comments'):
                result = {'id': 201, 'user': {'id': 8}, 'body': payload['body'],
                          'issue_url': 'https://api.github.com' + self.prefix + '/issues/91'}
                target = self.comments
            else:
                raise AssertionError(('unexpected POST', path))
            if path not in self.drop:
                target.append(result)
            if path in self.lose_ack:
                raise TimeoutError('fake uncertain acknowledgement')
        elif method == 'GET':
            if path == '/user':
                result = {'id': 8, 'login': 'fixture-user'}
            elif path == self.prefix:
                result = {'full_name': 'acme/widgets', 'id': 7, 'default_branch': 'main'}
            elif path == self.prefix + '/issues/291':
                result = {**self.facts[291], 'state': 'open',
                    'repository_url': 'https://api.github.com' + self.prefix}
            elif path.startswith(self.prefix + '/pulls?'):
                page = int(path.split('page=')[-1])
                result = self.pulls[(page - 1) * 100:page * 100]
            elif path == self.prefix + '/pulls/91':
                result = self.pulls[0]
            elif path.startswith(self.prefix + '/issues/91/comments?'):
                page = int(path.split('page=')[-1])
                result = self.comments[(page - 1) * 100:page * 100]
            else:
                raise AssertionError(('unexpected GET', path))
        else:
            raise AssertionError(('unexpected method', method))
        result = copy.deepcopy(result)
        if self.mutate:
            result = self.mutate(path, result)
        return json.dumps(result).encode()


class TrustedHostTests(unittest.TestCase):
    def setUp(self):
        # Guard the only real HTTP seam for every positive and negative test.
        self.network = patch.object(host.http.client, 'HTTPSConnection',
                                    side_effect=AssertionError('REAL NETWORK PROHIBITED'))
        self.network_mock = self.network.start()
        self.addCleanup(self.network.stop)
        self.source = fixtures.MainOrchestratorTest('runTest')
        self.source.setUp()
        self.addCleanup(self.source.doCleanups)
        self.source.initial_fixture_patch.stop()
        self.temp = self.source.fixture.temp_root
        self.journal_dir = self.temp / 'b1-recovery'
        self.journal_dir.mkdir(mode=0o700)
        self.journal = RecoveryJournal(self.journal_dir)
        self.rest = FakeREST(self.source.facts)
        self.authorizations, self.intents, self.pushes = [], [], []
        self.deny = None
        self.deny_snapshot = False
        self.deny_content = False
        self.deny_intent = False
        self.engines = {}
        for operation in OPERATIONS:
            policy = prerequisites.OperationPolicy(operation, 'b1_fixture', 'execution',
                (prerequisites.CheckSpec('fixture_facts', 'fixture_host'),))
            self.engines[operation] = prerequisites.OperationPrerequisites(policy,
                checks={'fixture_facts': lambda context: prerequisites.CheckResult(
                    context.binding, 'SATISFIED', 'fixture_current', {}, {}, 'none')})
        self.choice = host.HostTask(291, self.source.branch_ref, self.source.base,
                                   self.source.pub.record_id, self.source.pub.contract_id)
        self.worktree = Path(IssueWorktreeAllocator(self.source.store).ensure(
            task='291', branch_ref=self.choice.branch_ref, base_revision=self.choice.base_revision,
            expected_contract_id=self.choice.contract_id).worktree)

    def tearDown(self):
        self.network_mock.assert_not_called()
        self.assertEqual(self.source.prompts(), [])  # no model Task turns

    def authorize(self, request):
        self.authorizations.append(request)
        return (request.repository == 'acme/widgets' and request.task == '291'
                and request.operation != self.deny)

    def record(self, intent):
        self.intents.append(intent)
        return not self.deny_intent

    def publish(self, intent):
        self.pushes.append(intent)
        self.rest.subject = intent.subject
        git_fixture.git('push', '--no-follow-tags', 'origin',
                        f'{intent.subject}:{intent.branch_ref}', cwd=self.worktree)

    def arguments(self):
        return dict(primary_store=self.source.store, tasks={291: self.choice},
            repository_id=7, principal_id=8, default_branch_ref='refs/heads/main',
            execute=self.rest, store_for_worktree=self.source.fixture._writer,
            publish_branch=self.publish, prerequisites_by_operation=self.engines,
            authorize=self.authorize, record_intent=self.record,
            content_policy=lambda request: not self.deny_content,
            authorize_snapshot=lambda request: not self.deny_snapshot,
            journal=self.journal, report='B1 synthetic explicit handoff.')

    def install(self, **overrides):
        args = {**self.arguments(), **overrides}
        return host.assemble_host(**args).initial_collaboration_for_issue(291, self.worktree)

    def establish(self, **overrides):
        installed = self.install(**overrides)
        return installed.facade.establish(installed.report, installed.metadata_provider)

    def prepare_pr(self):
        installed = self.install()
        installed.facade.bootstrap()
        installed.facade.push()
        pr = installed.facade.ensure_draft_pr()
        return installed, pr

    def test_success_real_interfaces_and_reconnect_without_duplicate(self):
        first = self.establish()
        self.assertEqual(first.pull_request, 91)
        self.assertEqual(first.comment_id, 201)
        self.assertEqual(len(self.rest.posts), 2)
        second = self.establish(journal=RecoveryJournal(self.journal_dir))
        self.assertEqual(first, second)
        self.assertEqual(len(self.rest.posts), 2)
        self.assertEqual(len(self.pushes), 1)
        self.source.fixture._writer(self.worktree).confirm(first.metadata_commit,
            required_objects=(('task-view-snapshot', first.snapshot_id, '291', first.subject),))

    def test_all_missing_callbacks_fail_before_external_calls(self):
        for name in ('execute', 'store_for_worktree', 'publish_branch', 'authorize',
                     'record_intent', 'content_policy', 'authorize_snapshot'):
            with self.subTest(name=name), self.assertRaises(TrustedHostError):
                host.assemble_host(**{**self.arguments(), name: None})
        self.assertEqual(self.rest.calls, [])

    def test_missing_prerequisites_fail_before_calls(self):
        with self.assertRaises(TrustedHostError):
            host.assemble_host(**{**self.arguments(), 'prerequisites_by_operation': {}})
        self.assertEqual(self.rest.calls, [])

    def test_denied_authority_before_branch_or_github_publication(self):
        self.deny = 'collaboration.bootstrap'
        with self.assertRaises(TaskCollaborationError):
            self.establish()
        self.assertEqual(self.pushes, [])
        self.assertEqual(self.rest.posts, [])

    def test_denied_content_and_intent_do_not_create_pr(self):
        self.deny_content = True
        with self.assertRaises(TaskCollaborationError):
            self.establish()
        self.assertEqual(self.rest.posts, [])
        self.deny_content, self.deny_intent = False, True
        with self.assertRaises(TaskCollaborationError):
            self.establish()
        self.assertEqual(self.rest.posts, [])

    def test_snapshot_denied_and_retry_no_duplicate_pr(self):
        self.deny_snapshot = True
        with self.assertRaises(TaskCollaborationError):
            self.establish()
        self.assertEqual(len(self.rest.posts), 1)
        self.deny_snapshot = False
        self.establish()
        self.assertEqual(len(self.rest.posts), 2)

    def test_cross_issue_branch_record_contract_and_worktree(self):
        for field, value in [('issue', 292), ('branch_ref', 'refs/heads/issue-291-wrong'),
                             ('record_id', 'a' * 64), ('contract_id', 'b' * 64)]:
            with self.subTest(field=field), self.assertRaises(Exception):
                self.install(tasks={291: replace(self.choice, **{field: value})})
        capabilities = host.assemble_host(**self.arguments())
        with self.assertRaises(TrustedHostError):
            capabilities.initial_collaboration_for_issue(291, self.source.project)
        with self.assertRaises(TrustedHostError):
            capabilities.read_issue(292)
        self.assertEqual(self.rest.posts, [])

    def test_no_cas_no_write(self):
        with self.assertRaisesRegex(TrustedHostError, 'metadata_cas_required'):
            self.install(store_for_worktree=lambda p: host.MetadataStore(p, 'acme/widgets'))
        self.assertEqual(self.rest.calls, [])

    def test_issue_reader_maps_real_facts_not_extra_authority(self):
        self.rest.mutate = lambda path, d: {**d, 'authorize': True, 'host_provider': '/evil'}
        facts = host.assemble_host(**self.arguments()).read_issue(291)
        self.assertEqual(facts, self.source.facts[291])
        self.assertEqual(self.rest.posts, [])

    def test_bad_issue_body_state_and_identity_fail_without_publication(self):
        capabilities = host.assemble_host(**self.arguments())
        for field, value in [('body', False), ('body', []), ('state', 'unknown'),
                             ('number', True), ('repository_url', 'https://evil.invalid')]:
            def mutate(path, data, f=field, v=value):
                if path.endswith('/issues/291'):
                    data[f] = v
                return data
            self.rest.mutate = mutate
            with self.subTest(field=field, value=value), self.assertRaises(Exception):
                capabilities.read_issue(291)
        self.assertEqual(self.rest.posts, [])

    def test_lost_ack_pr_and_comment_exactly_reconciled(self):
        self.rest.lose_ack = {self.rest.prefix + '/pulls',
                              self.rest.prefix + '/issues/91/comments'}
        receipt = self.establish()
        self.assertEqual(receipt.comment_id, 201)
        self.establish()
        self.assertEqual(len(self.rest.posts), 2)

    def test_uncertain_pr_no_result_never_reposts_even_on_reconnect(self):
        self.rest.drop = {self.rest.prefix + '/pulls'}
        self.rest.lose_ack = self.rest.drop.copy()
        for _ in range(2):
            with self.assertRaises(TaskCollaborationError):
                self.establish(journal=RecoveryJournal(self.journal_dir))
        self.assertEqual(len(self.rest.posts), 1)

    def test_uncertain_comment_no_result_never_recaptures_or_reposts(self):
        self.rest.drop = {self.rest.prefix + '/issues/91/comments'}
        self.rest.lose_ack = self.rest.drop.copy()
        with self.assertRaises(TaskCollaborationError):
            self.establish()
        before = {p.name: p.read_bytes() for p in self.journal_dir.iterdir()}
        with self.assertRaises(TaskCollaborationError):
            self.establish(journal=RecoveryJournal(self.journal_dir))
        self.assertEqual(len(self.rest.posts), 2)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.journal_dir.iterdir()})

    def test_interrupt_before_publication_blocks_without_recapture(self):
        installed, pr = self.prepare_pr()
        # Preserve exact candidate before publication, then simulate death.
        create = self.journal.create

        def interrupt(key, record):
            create(key, record)
            raise RuntimeError('fixture interruption before CAS')

        with patch.object(self.journal, 'create', side_effect=interrupt):
            with self.assertRaises(Exception):
                installed.metadata_provider('acme/widgets', '291', self.choice.branch_ref,
                                            91, pr.subject)
        before = {p.name: p.read_bytes() for p in self.journal_dir.iterdir()}
        again = self.install(journal=RecoveryJournal(self.journal_dir))
        with patch.object(TaskViewSnapshots, 'capture', side_effect=AssertionError('no recapture')):
            with self.assertRaisesRegex(TrustedHostError, 'snapshot_recovery_required'):
                again.metadata_provider('acme/widgets', '291', self.choice.branch_ref, 91, pr.subject)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.journal_dir.iterdir()})

    def test_interrupt_after_remote_cas_recovers_exact_candidate_not_tip(self):
        installed, pr = self.prepare_pr()
        store = installed.facade._store
        original = store._direct_ref_cas

        def applied_then_interrupted(candidate, old):
            original(candidate, old)
            raise KeyboardInterrupt('fixture death after CAS')

        with patch.object(store, '_direct_ref_cas', side_effect=applied_then_interrupted):
            with self.assertRaises(KeyboardInterrupt):
                installed.metadata_provider('acme/widgets', '291', self.choice.branch_ref, 91, pr.subject)
        again = self.install(journal=RecoveryJournal(self.journal_dir))
        binding = again.metadata_provider('acme/widgets', '291', self.choice.branch_ref, 91, pr.subject)
        # A new unrelated metadata object advances the tip, not the binding.
        writer = self.source.fixture._writer(self.worktree)
        import metadata_codec as codec
        _, data = codec.encode_object('contract', 'acme/widgets', '999', pr.subject,
                                     {'note': 'unrelated immutable object'})
        newer = writer.publish([data])
        self.assertNotEqual(binding.metadata_commit, newer)
        same = self.install().metadata_provider('acme/widgets', '291', self.choice.branch_ref, 91, pr.subject)
        self.assertEqual(binding, same)

    def test_transport_writes_require_real_one_use_facade_permit(self):
        installed, pr = self.prepare_pr()
        draft = next(i for i in self.intents if i.operation == 'collaboration.draft_pr')
        i = host.CreateDraftIntent('acme/widgets', '291', self.choice.branch_ref,
            'refs/heads/main', pr.subject, self.source.facts[291]['title'],
            self.source.facts[291]['body'], draft)
        with self.assertRaisesRegex(TrustedHostError, 'unapproved_write_intent'):
            installed.facade._github.create_draft(i)
        with self.assertRaises(TrustedHostError):
            installed.facade._github.create_draft(replace(i, repository='evil/widgets'))
        self.assertEqual(len(self.rest.posts), 1)

    def test_altered_approved_comment_content_cannot_use_permit(self):
        installed, _ = self.prepare_pr()
        gh = installed.facade._github
        post = gh.post_comment
        with patch.object(gh, 'post_comment', side_effect=lambda i: post(
                replace(i, body=i.body + '\nUnapproved prose.'))):
            with self.assertRaises(TaskCollaborationError):
                installed.facade.establish(installed.report, installed.metadata_provider)
        self.assertEqual(self.rest.comments, [])
        self.assertEqual(len(self.rest.posts), 1)

    def test_main_host_route_stops_before_session_and_reconnect_reuses_checkpoint(self):
        from tools import kagari_vnext_main_service as service
        from tools import kagari_vnext_main as main
        plan = {'project_root': str(self.source.project), 'repository': 'acme/widgets',
                'runtime_home': str(self.source.runtime),
                'tasks': [{'issue': 291, 'branch_ref': self.choice.branch_ref,
                           'base_revision': self.choice.base_revision,
                           'contract_id': self.choice.contract_id, 'port': self.source.ports[291]}]}
        for _ in range(2):
            controller = service.build_main(plan, host=host.assemble_host(**self.arguments()))
            with patch.object(main, 'start_root_session', side_effect=main.MainError(
                    'SYNTHETIC_STOP_NO_MODEL')) as start:
                with self.assertRaisesRegex(main.MainError, 'SYNTHETIC_STOP_NO_MODEL'):
                    self.source.dispatch(controller, 291)
                start.assert_called_once()
        self.assertEqual(len(self.rest.posts), 2)
        self.assertFalse((self.source.runtime / 'issue-291' / 'prompt.pending.json').exists())

    def test_conflicting_source_or_unknown_recovery_state_blocks(self):
        installed, pr = self.prepare_pr()
        identity = ['acme/widgets', '291', self.choice.branch_ref, 91, pr.subject,
                    self.choice.record_id, self.choice.contract_id]
        self.journal.create(host._key(['initial-explicit-handoff', identity]),
                            {'state': 'unknown'})
        with self.assertRaises(TrustedHostError):
            installed.metadata_provider('acme/widgets', '291', self.choice.branch_ref, 91, pr.subject)
        self.assertEqual(self.rest.comments, [])

    def test_cross_repo_issue_and_pr_facts(self):
        installed = self.install()
        for field, value in [('repository_url', 'https://api.github.com/repos/evil/widgets'),
                             ('number', 999), ('pull_request', {})]:
            self.rest.mutate = lambda p, d, f=field, v=value: {**d, f: v}
            with self.subTest(field=field), self.assertRaises(Exception):
                installed.facade._github.issue(291)
        self.rest.mutate = None
        installed, pr = self.prepare_pr()
        original = copy.deepcopy(self.rest.pulls[0])
        mutations = [lambda d: d['base']['repo'].update(full_name='evil/widgets'),
                     lambda d: d['head'].update(ref='other'),
                     lambda d: d['head'].update(sha='a' * 40),
                     lambda d: d.update(number=92),
                     lambda d: d.update(draft=False)]
        for mutate in mutations:
            self.rest.pulls[0] = copy.deepcopy(original)
            mutate(self.rest.pulls[0])
            with self.assertRaises(Exception):
                installed.facade._github._exact_pull(91, pr.subject)

    def test_pagination_limits_duplicates_and_malformed_extra_entries(self):
        installed, _ = self.prepare_pr()
        gh = installed.facade._github
        for page in (0, True, 101, '1;evil'):
            with self.subTest(page=page), self.assertRaises(TrustedHostError):
                gh.pull_page(page)
        original = copy.deepcopy(self.rest.pulls[0])
        self.rest.pulls = [copy.deepcopy(original) for _ in range(101)]
        for n, p in enumerate(self.rest.pulls):
            p['number'] = n + 1
            p['head']['ref'] = 'unrelated-' + str(n)
        self.assertEqual(len(installed.facade._pulls_once()), 101)
        self.rest.pulls[100]['number'] = 1
        with self.assertRaisesRegex(TaskCollaborationError, 'pagination_duplicate'):
            installed.facade._pulls_once()
        self.rest.pulls = [original, {'authorize': True}]
        with self.assertRaises(TaskCollaborationError):
            installed.facade._pulls_once()

    def test_json_duplicate_oversized_wrong_shapes_and_principal(self):
        for payload in (b'{"id":8,"id":9}', b'[]', b'null', b'{"id":true,"login":"x"}',
                        b'x' * (1024 * 1024 + 1)):
            with self.subTest(payload=payload[:40]):
                installed = self.install()
                installed.facade._github._execute = lambda *args: payload
                with self.assertRaises(Exception):
                    installed.facade._github.principal()


class HTTPBoundaryTests(unittest.TestCase):
    def test_wrong_repo_method_or_redirect_not_followed(self):
        api = host.GitHubHTTPS('acme/widgets', 'test-token-not-a-credential')
        with patch.object(host.http.client, 'HTTPSConnection') as connection:
            for method, path in [('DELETE', '/repos/acme/widgets'),
                                 ('GET', '/repos/evil/widgets'),
                                 ('GET', 'https://evil.invalid'),
                                 ('GET', '/repos/acme/widgets/../other'),
                                 ('GET', '/repos/acme/widgets/issues/291/comments?per_page=100&page=101'),
                                 ('PATCH', '/repos/acme/widgets/pulls/91')]:
                with self.assertRaises(TrustedHostError):
                    api(method, path, None)
            connection.assert_not_called()
            response = connection.return_value.getresponse.return_value
            response.status = 302
            with self.assertRaises(TrustedHostError):
                api('GET', '/user', None)
            self.assertEqual(connection.call_count, 1)
            response.read.assert_not_called()

    def test_response_type_and_byte_limit_fail_closed(self):
        api = host.GitHubHTTPS('acme/widgets', 'test-token-not-a-credential')
        with patch.object(host.http.client, 'HTTPSConnection') as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.getheader.return_value = 'text/html'
            with self.assertRaises(TrustedHostError):
                api('GET', '/user', None)
            response.read.assert_not_called()
            response.getheader.return_value = 'application/json'
            response.read.return_value = b'x' * (1024 * 1024 + 1)
            with self.assertRaises(TrustedHostError):
                api('GET', '/user', None)
            response.read.assert_called_once_with(1024 * 1024 + 1)

    def test_metadata_patch_is_closed_without_any_executor_call(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            gh = host.BoundedGitHubTransport(repository='acme/widgets', repository_id=7,
                principal_id=8, issue=291, branch_ref='refs/heads/issue-291',
                default_branch_ref='refs/heads/main', execute=lambda *a: calls.append(a),
                consume=lambda *a: False, journal=RecoveryJournal(Path(d)))
            intent = host.EditMetadataIntent('acme/widgets', '291', 91,
                'refs/heads/issue-291', 'a' * 40, 'old', 'body', 'new', 'body', None)
            with self.assertRaisesRegex(TrustedHostError, 'metadata_edit_requires_external_exclusion'):
                gh.edit_metadata(intent)
            self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
