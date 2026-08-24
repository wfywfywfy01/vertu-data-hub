"""Conservative high-sensitivity detection before content is indexed."""
from __future__ import annotations

import re


_ASSIGNMENT_PATTERNS = {
    "password": re.compile(r"(?i)(?:password|passwd|pwd|密码)\s*[:=]\s*[^\s,;]{6,}"),
    "api_key": re.compile(
        r"(?i)(?:api[_ -]?key|access[_ -]?key|secret[_ -]?key|api密钥)\s*[:=]\s*[^\s,;]{8,}"
    ),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "china_identity": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
}
_SENSITIVE_FILENAME = re.compile(
    r"(?i)(?:身份证|银行卡|密码|私钥|credentials?|password|api[_ -]?key|secret[_ -]?key)"
)
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)")


def _luhn(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    if not 16 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def high_sensitivity_reasons(
    text: str = "", *, filename: str = "", sensitivity: str = ""
) -> list[str]:
    reasons = []
    if sensitivity == "restricted":
        reasons.append("restricted_classification")
    if _SENSITIVE_FILENAME.search(filename or ""):
        reasons.append("sensitive_filename")
    value = text or ""
    for name, pattern in _ASSIGNMENT_PATTERNS.items():
        if pattern.search(value):
            reasons.append(name)
    if any(_luhn(match.group()) for match in _CARD_CANDIDATE.finditer(value)):
        reasons.append("payment_card")
    return sorted(set(reasons))
