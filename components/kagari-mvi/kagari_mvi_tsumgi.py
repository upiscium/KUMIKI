"""Direct TSUMGI calls for Leafs that do not edit: review, summary, classification (ADR-0010 D1, #434).

One chat-completions request, no agent framework: an agent's own prompt and tool
definitions would use up TSUMGI's input limit (#427). Kept out of kagari_mvi.py and
kagari_mvi_exec.py, which are at their 800-line limit (ADR-0010 Consequences).
"""
from __future__ import annotations

import datetime
import json
import math
import os
from typing import Any, Callable, Dict, Optional

TSUMGI_URL, TSUMGI_MODEL = "https://tsumgi.arc.upiscium.dev/v1", "qwen3.6-35b-a3b"
INPUT_TOKEN_LIMIT = 10_240  # TSUMGI's effective_limits.input_tokens for the model
MAX_OUTPUT_TOKENS = 2_048


def _setting(name: str, default: str, low: float, high: float) -> float:
    """A positive number from the environment; a bad value stops with the variable's name."""
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        value = float("nan")
    if not low < value <= high:
        raise SystemExit("kagari_mvi: %s must be a number in (%g, %g]" % (name, low, high))
    return value


# The input is estimated from its UTF-8 size before sending, and never truncated: an input
# over the limit is not sent. 2.5 bytes per token overestimates mixed code and Japanese
# (#434). Override with KAGARI_MVI_TSUMGI_BYTES_PER_TOKEN / KAGARI_MVI_TSUMGI_MARGIN.
BYTES_PER_TOKEN = _setting("KAGARI_MVI_TSUMGI_BYTES_PER_TOKEN", "2.5", 0, 100)
MARGIN = _setting("KAGARI_MVI_TSUMGI_MARGIN", "0.95", 0, 1)


def estimate_tokens(text: str, bytes_per_token: float = BYTES_PER_TOKEN) -> int:
    return math.ceil(len(text.encode("utf-8")) / bytes_per_token)


def max_input_bytes(bytes_per_token: float = BYTES_PER_TOKEN, margin: float = MARGIN) -> int:
    """The largest input, in UTF-8 bytes, that is still sent."""
    return int(INPUT_TOKEN_LIMIT * margin * bytes_per_token)


def chat(run: Callable[..., str], system: str, user: str, purpose: str,
         record: Optional[str] = None, bytes_per_token: float = BYTES_PER_TOKEN,
         margin: float = MARGIN) -> Dict[str, Any]:
    """Ask TSUMGI once. Returns text, error (None on success) and the token counts.

    `<think>` reasoning is dropped; a reply cut off at max_tokens, or with unclosed
    reasoning, is an error, so a draft answer is never used as the answer. Every call,
    sent or not, is appended to `record` (JSON lines) when given.
    """
    result: Dict[str, Any] = {"text": "", "error": None, "sent": False, "model": TSUMGI_MODEL,
                              "estimated_input_tokens": None, "input_tokens": None,
                              "output_tokens": None}
    try:
        estimated = result["estimated_input_tokens"] = estimate_tokens(system + user, bytes_per_token)
        if estimated > INPUT_TOKEN_LIMIT * margin:
            raise ValueError("the input is too large for TSUMGI (estimated %d tokens, limit %d)"
                             % (estimated, int(INPUT_TOKEN_LIMIT * margin)))
        body = {"model": TSUMGI_MODEL, "temperature": 0, "max_tokens": MAX_OUTPUT_TOKENS,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        result["sent"] = True
        out = run(["curl", "-sS", "--fail-with-body", "-m", "300", "-H",
                   "Content-Type: application/json", "--data-binary", "@-",
                   TSUMGI_URL + "/chat/completions"], input=json.dumps(body, ensure_ascii=False))
        reply = json.loads(out)
        usage = reply.get("usage") or {}
        result.update(input_tokens=usage.get("prompt_tokens"),
                      output_tokens=usage.get("completion_tokens"))
        choice = reply["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("TSUMGI stopped at max_tokens")
        text = (choice["message"]["content"] or "").rsplit("</think>", 1)[-1]
        if "<think>" in text:
            raise ValueError("TSUMGI's reasoning was not closed")
        result["text"] = text
    except Exception as error:  # noqa: BLE001 - every failure is an error result, never an answer
        detail = getattr(error, "output", "") or str(error)
        result["error"] = " ".join(detail.split())[:200]
    if record:
        os.makedirs(os.path.dirname(record), mode=0o700, exist_ok=True)
        entry = dict(result, text=None, purpose=purpose,
                     time=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
        with open(record, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return result
