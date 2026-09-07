"""What a child process is allowed to inherit.

Ratatosk spawns children in two places — the `Bash` tool and the hook runtime —
and neither passed an explicit ``env=``, so both inherited the full parent
environment. With the API key living in ``os.environ`` that meant a
model-invoked ``Bash`` call running ``env`` printed the key into the transcript,
and under ``--trust`` it did so without asking.

The posture is kartikeya's, from `sandbox.py`: a child receives credentials only
when something deliberately grants them, never by default. Here that reduces to
an allowlist — this runtime has no sandbox to negotiate with, so the boundary is
the environment dict itself.
"""
from __future__ import annotations

import os
import re

#: Names passed through verbatim. Kept deliberately short: the test of a name
#: belonging here is whether a child *breaks* without it, not whether a child
#: might find it useful.
PASSTHROUGH: tuple[str, ...] = (
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "TMPDIR",
    "PWD",
    # Termux. Without these every child on the phone node fails to start: the
    # whole prefix tree lives under $PREFIX and the loader needs its lib dir.
    "PREFIX",
    "LD_LIBRARY_PATH",
    "ANDROID_DATA",
    "ANDROID_ROOT",
    # Python, so a child interpreter behaves like the parent's.
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONIOENCODING",
    "VIRTUAL_ENV",
    # Fleet placement. These are paths, not credentials.
    "WILLOW_HOME",
    "WILLOW_ROOT",
    "WILLOW_STORE_ROOT",
    "WILLOW_AGENT_NAME",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "RATATOSK_APP_ID",
    "RATATOSK_GROVE_CHANNEL",
    "RATATOSK_SESSION_DIR",
)

#: Names that never reach a child even if they somehow match the allowlist.
#: Belt to the allowlist's braces — the allowlist is what actually enforces
#: this, but a future edit that widens it should not silently widen these too.
SECRET_RE = re.compile(
    r"(API_KEY|_TOKEN$|_SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_KEY|SESSION_KEY)",
    re.IGNORECASE,
)

#: Loader-injection vectors. `LD_LIBRARY_PATH` is on the allowlist because
#: Termux cannot run without it; `LD_PRELOAD` has no such excuse.
NEVER: frozenset[str] = frozenset({"LD_PRELOAD", "LD_AUDIT", "DYLD_INSERT_LIBRARIES"})


def child_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Build the environment a spawned child receives.

    Allowlist, not denylist: a name absent from PASSTHROUGH is dropped whether
    or not anyone anticipated it. A new provider key added by a future BYOK
    change is therefore excluded by default rather than by remembering to add
    a rule for it.
    """
    source = os.environ if base is None else base
    out: dict[str, str] = {}
    for name in PASSTHROUGH:
        if name in NEVER or SECRET_RE.search(name):
            continue
        value = source.get(name)
        if value is not None:
            out[name] = value
    return out
