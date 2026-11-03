"""Isolated Host archiver for source-only Metadata v2. No deletion capability."""

from collections.abc import Callable

from metadata_v2 import (
    ARCHIVE_REF, InvalidMetadata, MetadataV2, Update, Writer, construct, issue_ref, union,
)


# Host authorization is separately verified live Task terminality + no writers,
# bound to this exact Update and a server retention policy identifier. A Record
# disposition or JSON assertion is deliberately NOT accepted as proof.
TerminalVerifier = Callable[[Update, float], bool]


def archive_issue(store: MetadataV2, task: str, *, retention_policy: str | None = None,
                  verify_terminal: TerminalVerifier | None = None,
                  archive_writer: Writer | None = None) -> str:
    """Construct true (archive A, Issue C) union and request exact non-force CAS.

    Trusted writer rechecks verifier provenance and all Update preconditions
    atomically with its mutation; callback execution here is not a lock.
    Missing capability/policy refuses before any construction or Host effects.
    Even confirmed archive publication NEVER authorizes production deletion.
    """
    ref = issue_ref(task)
    if (type(retention_policy) is not str or not retention_policy.strip()
            or len(retention_policy) > 512 or not callable(verify_terminal)
            or not callable(archive_writer)):
        raise InvalidMetadata("archive requires retention policy, terminal verifier and trusted writer")
    v = store.validation()
    archive_oid = v.observe(store.observer, ARCHIVE_REF)
    issue_oid = v.observe(store.observer, ref)
    archive = v.archive(archive_oid)
    issue = v.issue(issue_oid, task)
    files = union(archive.snapshot, issue.snapshot)
    bundle = construct(files, (archive_oid, issue_oid))
    request = Update(store.repository, store.genesis, task, ARCHIVE_REF,
                     archive_oid, issue_oid, bundle.commit, retention_policy)
    v.check()
    try:
        verified = verify_terminal(request, v.deadline)
    except Exception as error:
        raise InvalidMetadata("terminal verification unavailable; no archive mutation") from error
    v.check()
    if verified is not True:
        raise InvalidMetadata("terminal Task/no writers not separately verified")
    return store._publish(v, request, bundle, archive_writer)
