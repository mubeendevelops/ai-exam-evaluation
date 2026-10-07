"""The strict reply: one JSON object ``{"credit": 0 | 0.5 | 1, "reason": "<text>"}``, nothing
else (no code fence, no extra keys, no string credit). Anything else is a bad reply, which the
scorer retries and then gives up on; it is never repaired or guessed at."""

import json
from decimal import Decimal

REASON_MAX = 400
_CREDITS = (Decimal(0), Decimal("0.5"), Decimal(1))


class BadReplyError(ValueError):
    """The model's reply is not the strict JSON. Carries no part of the reply."""


def parse_reply(text: str) -> tuple[Decimal, str]:
    try:
        data = json.loads(text, parse_float=Decimal, parse_constant=_refuse)
    except (ValueError, RecursionError):
        raise BadReplyError("the reply is not JSON") from None
    if not isinstance(data, dict) or set(data) != {"credit", "reason"}:
        raise BadReplyError("the reply is not an object with exactly credit and reason")
    credit = data["credit"]
    # bool is an int in Python: True would pass for 1.
    if isinstance(credit, bool) or not isinstance(credit, int | Decimal):
        raise BadReplyError("credit is not a number")
    if Decimal(credit) not in _CREDITS:
        raise BadReplyError("credit is not 0, 0.5 or 1")
    reason = data["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise BadReplyError("reason is not text")
    reason = " ".join(reason.split())
    if len(reason) > REASON_MAX:
        reason = reason[: REASON_MAX - 1].rstrip() + "…"
    return Decimal(credit), reason


def _refuse(_name: str) -> object:
    raise ValueError("NaN and Infinity are not JSON")
