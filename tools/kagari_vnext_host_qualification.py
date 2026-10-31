"""#291 B2a source-only readiness probe, NOT an Active Runtime or dispatcher.

Only a trusted in-process caller can inject B1 capabilities. This observation
does not exercise/approve writes, install anything, or grant future execution.
No CLI, callback loader, credential discovery or new ownership mechanism.
"""
from __future__ import annotations

from pathlib import Path

if __package__:
    from . import kagari_vnext_attachment_gate as gate
    from . import kagari_vnext_main_service as service
    from .kagari_vnext_main import InitialCollaboration, MainError
else:
    import kagari_vnext_attachment_gate as gate
    import kagari_vnext_main_service as service
    from kagari_vnext_main import InitialCollaboration, MainError

from metadata_ref import MetadataStore
from task_collaboration import TaskCollaboration


def qualify_host(plan: dict, *, state_home: Path, package_root: Path,
                 host: service.MainHostCapabilities | None = None,
                 runtime_preview: bool = False) -> dict:
    """Observe already-prepared bindings under #329 and the existing Main owner.

    The caller must explicitly opt in and inject ``assemble_host``'s result.
    Existing registered Worktrees/#191 records are required; no allocation or
    bootstrap occurs. Reads can fetch local Git objects; the existing owner
    can create its lock file. No product/ref/index, journal or runtime write is
    requested. Callbacks remain trusted host code, not sandboxed by this probe.

    Successful observation ALWAYS returns BLOCKED_LIVE: #256 activation and
    external publisher/CAS qualification cannot be inferred from callability.
    The report is not a receipt or a capability. Gate exit revalidation must
    succeed before it is returned; partial/uncertain observations raise.
    """
    if runtime_preview is not True:
        raise MainError('HOST_RUNTIME_PREVIEW_DISABLED')
    if type(host) is not service.MainHostCapabilities:
        raise MainError('MAIN_HOST_CAPABILITIES_INVALID')
    with gate.hold(Path(plan['project_root']), state_home, package_root) as attachment:
        main = service.build_main(plan, host=host)
        with service.MainSupervisor(main) as owner:
            with main._main_owner_guard:
                owner._assert_owned(main)
                observed = []
                for number in sorted(main.approved):
                    choice, _ = main._spec(number)
                    worktree = main.store.root / '.worktrees' / str(number)
                    installed = host.initial_collaboration_for_issue(number, worktree)
                    if (type(installed) is not InitialCollaboration
                            or type(installed.facade) is not TaskCollaboration
                            or not callable(installed.metadata_provider)
                            or type(installed.report) is not str
                            or not installed.report.strip()
                            or len(installed.report.encode("utf-8")) > 16_384):
                        raise MainError('MAIN_INITIAL_COLLABORATION_INVALID')
                    facade = installed.facade
                    store = facade._store
                    if (type(store) is not MetadataStore
                            or store.root != worktree
                            or store.repository != main.store.repository
                            or store.remote != main.store.remote
                            or facade.repository != main.store.repository
                            or facade.task != str(number)
                            or facade.branch_ref != choice.branch_ref
                            or facade._base_revision != choice.base_revision
                            or not callable(store._direct_ref_cas)):
                        raise MainError('MAIN_INITIAL_COLLABORATION_MISMATCH')
                    facade._read_principal()
                    facade._read_repository()
                    # Recheck Task/Issue facts after provider observation too.
                    main._spec(number, metadata_store=store)
                    observed.append(number)
                owner._assert_owned(main)
                report = {
                    'schema': 1, 'status': 'BLOCKED_LIVE',
                    'observation': 'HOST_BINDINGS_OBSERVED_ONLY',
                    'authority': 'PRECONDITION_NOT_EXECUTION_AUTHORITY',
                    'repository': main.store.repository,
                    'issues': observed,
                    'attachment_receipt_sha256': attachment['attachment_receipt_sha256'],
                    'blockers': ['ACTIVE_RUNTIME_INSTALL_STOP_UNIMPLEMENTED_256',
                                 'SOURCE_GUARD_PUBLISHER_NOT_QUALIFIED',
                                 'GITHUB_DIRECT_REF_CAS_NOT_QUALIFIED'],
                    'runtime_installed': False, 'main_started': False,
                    'task_execution_authorized': False,
                    'model_or_session_started': False,
                }
    return report
