"""Mask credential-shaped strings on their way out.

A backstop, not a control. The control is that a secret never enters the child
environment in the first place (`ratatosk.child_env`); this catches the cases
that control cannot see — a key pasted into a file the model reads, a token
echoed by a command that had it for its own reasons, a transcript exported to
somewhere less careful than the session directory.

Patterns are taken from `willow-mcp/src/willow_mcp/secret_scan.py`, which is
Apache-2.0 under the same copyright holder and pure stdlib `re`. Ordering
matters and is preserved: most specific first, so a PEM block is claimed whole
before a token rule can nibble at its base64 body.
"""

from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED:{kind}]"

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # PEM private key blocks (RSA/EC/OPENSSH/DSA/PGP or bare) — whole block.
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----"
            r".*?-----END (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY(?: BLOCK)?-----",
            re.DOTALL,
        ),
    ),
    # AWS access key id (long-term AKIA / temporary ASIA).
    ("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    # GitHub tokens: ghp_ (PAT), gho_/ghu_/ghs_/ghr_ (app/oauth/server/refresh).
    ("github_token", re.compile(r"\bgh[posur]_[A-Za-z0-9]{36,}\b")),
    # Slack tokens.
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # Google API key.
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # Stripe live secret / restricted keys.
    ("stripe_key", re.compile(r"\b(?:sk|rk)_live_[0-9a-zA-Z]{16,}\b")),
    # Provider secret keys with an `sk-` prefix (OpenAI / Anthropic `sk-ant-` /
    # others). Kept after stripe so `sk_live_` is claimed by the stripe rule.
    ("provider_api_key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{20,}\b")),
    # JSON Web Token: three base64url segments, header starts `eyJ`.
    (
        "jwt",
        re.compile(
            r"\beyJ[A-Za-z0-9_\-]{6,}\.eyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\b"
        ),
    ),
]


def redact(text: str) -> str:
    """Replace credential-shaped substrings with a labelled placeholder."""
    if not isinstance(text, str) or not text:
        return text
    for kind, pattern in _PATTERNS:
        text = pattern.sub(PLACEHOLDER.format(kind=kind), text)
    return text


def found(text: str) -> list[str]:
    """Which kinds `redact` would mask. For diagnostics; never logs the value."""
    if not isinstance(text, str) or not text:
        return []
    return [kind for kind, pattern in _PATTERNS if pattern.search(text)]
