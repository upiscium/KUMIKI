"""B2a offline composition: real Git/attachment/owner, fake GitHub, no model."""
from dataclasses import replace
from unittest.mock import Mock, patch
import unittest

from tests import test_kagari_vnext_trusted_host as b1
from tests import test_kagari_vnext_attach as attach_fixture
from tools import kagari_vnext_host_qualification as qualification
from tools import kagari_vnext_main_service as service
from tools import kagari_vnext_main as main_module
from tools import kagari_vnext_trusted_host as host_module
from task_record import TaskRecords, TaskRecordError


class QualificationTests(unittest.TestCase):
    def setUp(self):
        self.b1 = b1.TrustedHostTests('runTest')
        self.b1.setUp()
        self.addCleanup(self.b1.doCleanups)
        self.addCleanup(self.b1.tearDown)
        self.source = self.b1.source
        self.attachment = attach_fixture.AttachTests('runTest')
        self.attachment.setUp()
        self.addCleanup(self.attachment.doCleanups)
        # Reuse inert package/private state fixtures, but attach the SAME Git
        # Project as Main/#191. Never execute the package's dummy binaries.
        self.attachment.project = self.source.project
        self.attachment.operate('install')
        self.plan = {
            'project_root': str(self.source.project), 'repository': 'acme/widgets',
            'runtime_home': str(self.source.runtime),
            'tasks': [{'issue': 291, 'branch_ref': self.b1.choice.branch_ref,
                       'base_revision': self.b1.choice.base_revision,
                       'contract_id': self.b1.choice.contract_id,
                       'port': self.source.ports[291]}],
        }
        self.owner_patch = patch.object(service, '_host_state_home',
                                       return_value=self.source.fixture.temp_root)
        self.owner_patch.start()
        self.addCleanup(self.owner_patch.stop)
        for target, name in ((main_module, 'start_root_session'),
                             (main_module.RepositoryMain, 'dispatch'),
                             (qualification.TaskCollaboration, 'establish'),
                             (host_module.TaskViewSnapshots, 'capture')):
            guard = patch.object(target, name,
                                 side_effect=AssertionError('NO DISPATCH OR PUBLICATION'))
            guard.start()
            self.addCleanup(guard.stop)
        self.before = self.state()

    def state(self):
        git = b1.git_fixture.git
        return (git('rev-parse', 'HEAD', cwd=self.source.project),
                git('for-each-ref', '--format=%(refname) %(objectname)',
                    cwd=self.source.fixture.remote_path),
                (self.source.project / '.git/index').read_bytes(),
                {p.name: p.read_bytes() for p in self.b1.journal_dir.iterdir()})

    def tearDown(self):
        self.assertEqual(self.before, self.state())
        self.assertEqual(self.b1.rest.posts, [])
        self.assertEqual(self.b1.pushes, [])
        self.assertEqual(self.b1.authorizations, [])
        self.assertEqual(self.b1.intents, [])
        self.assertFalse(self.source.runtime.exists())

    def probe(self, **overrides):
        args = dict(state_home=self.attachment.home,
                    package_root=self.attachment.package,
                    host=host_module.assemble_host(**self.b1.arguments()),
                    runtime_preview=True)
        return qualification.qualify_host(self.plan, **{**args, **overrides})

    def test_disabled_preview_and_missing_injection_have_no_observations(self):
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value), self.assertRaisesRegex(
                    main_module.MainError, 'HOST_RUNTIME_PREVIEW_DISABLED'):
                self.probe(runtime_preview=value)
        with self.assertRaisesRegex(main_module.MainError, 'MAIN_HOST_CAPABILITIES_INVALID'):
            self.probe(host=None)
        self.assertEqual(self.b1.rest.calls, [])

    def test_approved_injection_only_observes_never_claims_activation_or_cas(self):
        self.b1.deny = 'collaboration.bootstrap'
        for _ in range(2):
            report = self.probe()
            self.assertEqual(report['status'], 'BLOCKED_LIVE')
            self.assertEqual(report['issues'], [291])
            self.assertIn('GITHUB_DIRECT_REF_CAS_NOT_QUALIFIED', report['blockers'])
            for key in ('runtime_installed', 'main_started',
                        'task_execution_authorized', 'model_or_session_started'):
                self.assertIs(report[key], False)
        self.assertTrue(self.b1.rest.calls)

    def test_missing_191_record_and_changed_contract_stop_before_github(self):
        for field, value in (('issue', 292), ('contract_id', 'a' * 64)):
            original = self.plan['tasks'][0].copy()
            self.plan['tasks'][0][field] = value
            with self.subTest(field=field), self.assertRaises(main_module.MainError):
                self.probe()
            self.plan['tasks'][0] = original
        self.assertEqual(self.b1.rest.calls, [])

    def test_repository_principal_and_issue_identity_drift_refuse(self):
        for target in ('repository', 'principal', 'issue', 'closed'):
            def mutate(path, data):
                if target == 'repository' and path == self.b1.rest.prefix:
                    data['id'] = 99
                if target == 'principal' and path == '/user':
                    data['id'] = 99
                if target == 'issue' and path.endswith('/issues/291'):
                    data['body'] = 'drift'
                if target == 'closed' and path.endswith('/issues/291'):
                    data['state'] = 'closed'
                return data
            self.b1.rest.mutate = mutate
            with self.subTest(target=target), self.assertRaises(
                    (main_module.MainError, b1.TaskCollaborationError)):
                self.probe()

    def test_no_cas_and_foreign_host_record_refuse(self):
        for override in (
            {'store_for_worktree': lambda p: host_module.MetadataStore(p, 'acme/widgets')},
            {'tasks': {291: replace(self.b1.choice, record_id='a' * 64)}},
        ):
            caps = host_module.assemble_host(**{**self.b1.arguments(), **override})
            with self.subTest(override=list(override)), self.assertRaises(host_module.TrustedHostError):
                self.probe(host=caps)

    def test_retired_task_stops_before_host_observation(self):
        # Explicit fixture-only human disposition prepares the negative state;
        # the probe itself must leave that new baseline byte-for-byte intact.
        records = TaskRecords(self.source.store, authorize=lambda request: (
            request.operation == 'disposition' and request.task == '291'
            and request.expected_record_id == self.b1.choice.record_id))
        records.set_disposition(task='291', branch_ref=self.b1.choice.branch_ref,
                                base_revision=self.source.base,
                                expected_record_id=self.b1.choice.record_id,
                                disposition={'kind': 'cancelled'})
        self.before = self.state()
        with self.assertRaisesRegex(main_module.MainError, 'TASK_RETIRED'):
            self.probe()
        self.assertEqual(self.b1.rest.calls, [])

    def test_missing_publisher_or_194_callbacks_cannot_assemble_host(self):
        for name in ('publish_branch', 'authorize', 'record_intent',
                     'content_policy', 'authorize_snapshot'):
            with self.subTest(name=name), self.assertRaises(host_module.TrustedHostError):
                host_module.assemble_host(**{**self.b1.arguments(), name: None})
        self.assertEqual(self.b1.rest.calls, [])

    def test_191_creation_is_not_authorized_by_selection_or_probe(self):
        for authorize in (None, lambda request: False):
            with self.subTest(authorize=authorize), self.assertRaises(TaskRecordError):
                TaskRecords(self.source.store, authorize=authorize).create(
                    task='292', branch_ref='refs/heads/issue-292',
                    base_revision=self.source.base, title='No grant', body='')
        self.plan['tasks'][0]['issue'] = 292
        with self.assertRaisesRegex(main_module.MainError, 'TASK_RECORD_ABSENT'):
            self.probe()

    def test_existing_owner_and_existing_attachment_gate_are_mandatory(self):
        caps = host_module.assemble_host(**self.b1.arguments())
        controller = service.build_main(self.plan, host=caps)
        with service.MainSupervisor(controller):
            with self.assertRaisesRegex(main_module.MainError, 'MAIN_ALREADY_ACTIVE'):
                self.probe(host=caps)
        with qualification.gate.hold(self.source.project, self.attachment.home,
                                     self.attachment.package):
            with self.assertRaises(qualification.gate.GateBlocked):
                self.probe(host=caps)
        self.assertEqual(self.b1.rest.calls, [])

    def test_absent_attachment_is_not_installed_by_probe(self):
        absent = self.attachment.base / 'unattached-state'
        absent.mkdir(mode=0o700)
        with self.assertRaises(qualification.gate.GateBlocked) as caught:
            self.probe(state_home=absent)
        self.assertEqual(caught.exception.code, 'GATE_ATTACHMENT_REQUIRED')
        self.assertEqual(list(absent.iterdir()), [])
        self.assertEqual(self.b1.rest.calls, [])

    def test_gate_exit_drift_cannot_return_report(self):
        caps = host_module.assemble_host(**self.b1.arguments())
        def observe(number, worktree):
            installed = caps.initial_collaboration_for_issue(number, worktree)
            (self.source.project / '.gitignore').write_text('/.worktrees/\n!/.worktrees/\n')
            return installed
        changed = service.MainHostCapabilities(caps.read_issue, observe)
        with self.assertRaises(qualification.gate.GateBlocked):
            self.probe(host=changed)

    def test_uncertain_reader_does_not_create_a_readiness_receipt(self):
        caps = host_module.assemble_host(**self.b1.arguments())
        def uncertain(number):
            raise TimeoutError('private details must not become a receipt')
        with self.assertRaisesRegex(main_module.MainError, 'GITHUB_ISSUE_UNAVAILABLE'):
            self.probe(host=service.MainHostCapabilities(
                uncertain, caps.initial_collaboration_for_issue))

    def test_claimed_or_uncertain_receipt_is_not_a_host_binding(self):
        caps = host_module.assemble_host(**self.b1.arguments())
        for receipt in (None, {'status': 'PASS', 'runtime_installed': True}):
            with self.subTest(receipt=receipt), self.assertRaisesRegex(
                    main_module.MainError, 'MAIN_INITIAL_COLLABORATION_INVALID'):
                self.probe(host=service.MainHostCapabilities(
                    caps.read_issue, lambda number, worktree: receipt))

    def host_with_initial_overrides(self, **overrides):
        caps = host_module.assemble_host(**self.b1.arguments())
        def observe(number, worktree):
            installed = caps.initial_collaboration_for_issue(number, worktree)
            return replace(installed, **overrides)
        return service.MainHostCapabilities(caps.read_issue, observe)

    def test_noncallable_metadata_provider_cannot_return_observation(self):
        for provider in (None, False, 'not-a-callback', object()):
            with self.subTest(provider=provider), self.assertRaisesRegex(
                    main_module.MainError, '^MAIN_INITIAL_COLLABORATION_INVALID$'):
                self.probe(host=self.host_with_initial_overrides(metadata_provider=provider))

    def test_invalid_initial_report_cannot_return_observation(self):
        class StringSubclass(str):
            pass
        invalid = {
            'none': None, 'integer': 1, 'bytes': b'report',
            'string_subclass': StringSubclass('report'),
            'empty': '', 'whitespace': ' \t\r\n\u2003',
            'ascii_16385_bytes': 'a' * 16_385,
            'utf8_16385_bytes': '\u00e9' * 8192 + 'a',
        }
        for label, report in invalid.items():
            with self.subTest(report=label), self.assertRaisesRegex(
                    main_module.MainError, '^MAIN_INITIAL_COLLABORATION_INVALID$'):
                self.probe(host=self.host_with_initial_overrides(report=report))

    def test_callable_provider_and_exact_utf8_report_limit_remain_observation_only(self):
        # A callable capability is required but must NOT be exercised by this
        # probe. Neither a valid report nor its exact byte limit grants writes.
        callback = Mock(side_effect=AssertionError('NO METADATA PUBLICATION'))
        for report in ('a' * 16_384, '\u00e9' * 8192, ' \treport\n'):
            with self.subTest(utf8_bytes=len(report.encode('utf-8'))):
                result = self.probe(host=self.host_with_initial_overrides(
                    metadata_provider=callback, report=report))
                self.assertEqual(result['status'], 'BLOCKED_LIVE')
                self.assertEqual(result['issues'], [291])
                self.assertIs(result['task_execution_authorized'], False)
        callback.assert_not_called()

    def test_client_only_lease_callable_is_never_exercised_or_live_qualified(self):
        # Callability is not #217 server-side evidence. Even a supplied callable
        # claiming a lease cannot turn this read-only observation into PASS.
        def client_only_force_with_lease(candidate, expected):
            self.fail('client-only lease must not be invoked by qualification')
        def store_for_worktree(path):
            return host_module.MetadataStore(path, 'acme/widgets',
                                             direct_ref_cas=client_only_force_with_lease)
        caps = host_module.assemble_host(**{
            **self.b1.arguments(), 'store_for_worktree': store_for_worktree})
        self.assertEqual(self.probe(host=caps)['status'], 'BLOCKED_LIVE')


class PublisherBoundaryTests(unittest.TestCase):
    def test_github_executor_offers_neither_ref_mutation_nor_graphql_cas(self):
        api = host_module.GitHubHTTPS('acme/widgets', 'synthetic-not-a-credential')
        with patch.object(host_module.http.client, 'HTTPSConnection') as connection:
            for method, path in (
                ('PATCH', '/repos/acme/widgets/git/refs/agentcore/metadata'),
                ('POST', '/repos/acme/widgets/git/refs'),
                ('POST', '/graphql'),
            ):
                with self.subTest(path=path), self.assertRaisesRegex(
                        host_module.TrustedHostError, 'endpoint_denied'):
                    api(method, path, b'{}')
            connection.assert_not_called()

    def test_source_guard_refusal_is_not_replaced_by_host_allow_policy(self):
        fixture = b1.TrustedHostTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        before = b1.git_fixture.git('for-each-ref', cwd=fixture.source.fixture.remote_path)
        calls = []
        def source_guard_refuses(intent):
            calls.append(intent)
            raise host_module.TrustedHostError('PINNED_SOURCE_GUARD_REQUIRED')
        with self.assertRaises(b1.TaskCollaborationError):
            fixture.establish(publish_branch=source_guard_refuses)
        self.assertEqual(len(calls), 1)
        self.assertEqual(fixture.rest.posts, [])
        self.assertEqual(fixture.pushes, [])
        self.assertEqual(before, b1.git_fixture.git(
            'for-each-ref', cwd=fixture.source.fixture.remote_path))


if __name__ == '__main__':
    unittest.main()
