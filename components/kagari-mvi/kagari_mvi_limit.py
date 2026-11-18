"""Usage limit (HTTP 429): nothing failed, so do not block the Issue; put it back and retry (#481)."""
from __future__ import annotations

from typing import List

from kagari_mvi_exec import UsageLimit

LIMIT_EXIT_CODE = 3


class LimitSteps:
    """Host-side deferral; the host class provides api_pages, repo_path, comment, remove_label, log."""

    def defer_for_limit(self, number: int, limit: UsageLimit, running: str) -> int:
        body = ("kagari-mvi: usage limit reached (not blocked). The Claude limit shared with the "
                "User's own sessions was hit; it resets at %s. The Issue was put back, and the "
                "next run tries it again." % limit.resets)
        self.log("#%d %s" % (number, body))
        try:
            comments: List[dict] = self.api_pages(self.repo_path("/issues/%d/comments" % number))
            if not any(comment.get("body") == body for comment in comments):  # once per reset time
                self.comment(number, body)
        except Exception as error:  # noqa: BLE001 - the label matters more than the comment
            self.log("could not post the usage-limit comment: %s" % error)
        try:
            self.remove_label(number, running)
        except Exception as error:  # noqa: BLE001 - say so: the Issue stays locked until a human acts
            self.log("could not remove %s: %s" % (running, error))
        return LIMIT_EXIT_CODE
