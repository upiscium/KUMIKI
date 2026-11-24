"""`work-once`: run-once then review-once, repeated while approved Issues remain (#535).

State stays in GitHub labels only. The number run-once just made `implemented` is passed to
review-once, which reads it directly, so the lagging label search cannot hide it.
"""
from typing import Any

USAGE_LIMIT = 3


def work_once(worker: Any, allowed: Any, workdir: str, max_issues: int) -> int:
    """Return 1 on any failure, else 3 if a usage limit stopped it, else 0."""
    seen: set = set()
    failed = limited = False
    for _ in range(max(max_issues, 1)):
        run_code = worker.run_once(allowed, workdir)
        number, implemented = worker.selected, worker.implemented
        if number is not None and number in seen:  # never twice in one call
            worker.log("#%d was already handled in this run" % number)
            break
        if number is not None:
            seen.add(number)
        review_code = worker.review_once(workdir, also=implemented)
        for code in (run_code, review_code):
            failed = failed or code not in (0, USAGE_LIMIT)
            limited = limited or code == USAGE_LIMIT
        if implemented is None or run_code != 0 or review_code != 0:
            break  # none left, blocked, or a usage limit: stop here
    return 1 if failed else USAGE_LIMIT if limited else 0
