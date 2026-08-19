"""Deterministic redaction before text reaches retrieval or derived previews."""
from __future__ import annotations

from dataclasses import dataclass
import re


EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+(?![\w.-])", re.UNICODE)
PHONE_CANDIDATE = re.compile(r"(?<!\w)\+?\d(?:[\d\s().-]{5,}\d)(?!\w)", re.UNICODE)


@dataclass(frozen=True)
class RedactionResult:
    text: str
    count: int


def redact_text(text: str | None) -> RedactionResult:
    value = text or ""
    value, email_count = EMAIL.subn("[REDACTED_EMAIL]", value)
    phone_count = 0

    def mask_phone(match: re.Match) -> str:
        nonlocal phone_count
        digits = sum(character.isdigit() for character in match.group())
        if not 7 <= digits <= 15:
            return match.group()
        phone_count += 1
        return "[REDACTED_PHONE]"

    value = PHONE_CANDIDATE.sub(mask_phone, value)
    return RedactionResult(value, email_count + phone_count)
