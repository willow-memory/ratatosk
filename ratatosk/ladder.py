"""The provider ladder — data, read and validated, never guessed.

Sealed decision 3613d55e (seat-runtime-provider-ladder-2026-09-20): a woken
seat's runtime is crown on a data-defined ladder of inference providers —
free tiers first, Ollama floor, Anthropic by task class. The ladder lives in
``provider_ladder.json`` beside this module. This module reads it, refuses a
malformed file, and answers one question: for a task class, which rungs may
be tried, in what order, and why each of the others may not.

Keys are *names* here (``key_env``), never values. The value is read from the
process environment at resolve time and handed to the client — it is never
stored on a rung and never written anywhere.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

LADDER_PATH = Path(__file__).parent / "provider_ladder.json"

#: The dialects a rung may declare. Anything else is a rung this runtime has
#: no client for, and it is refused rather than tried.
DIALECTS = ("openai", "anthropic", "ollama")

#: The rung statuses ``/doctor`` prints — one word each, in the order the
#: brief names them.
STATUS_USABLE = "usable"
STATUS_NO_KEY = "no-key"
STATUS_STALE = "stale-verify"
STATUS_REFUSED = "refused"


class LadderError(ValueError):
    """The ladder file is not a ladder. Names the field, never guesses past it."""


@dataclass(frozen=True)
class Rung:
    name: str
    provider: str
    dialect: str
    base_url: str
    key_env: str | None
    models: Mapping[str, str]
    verify_at: date | None
    status: str

    def model_for(self, task_class: str) -> str | None:
        return self.models.get(task_class)


@dataclass(frozen=True)
class RungVerdict:
    """One rung's answer to "may I be tried for this class?"."""

    rung: Rung
    status: str
    reason: str
    model: str | None = None

    @property
    def usable(self) -> bool:
        return self.status == STATUS_USABLE


@dataclass(frozen=True)
class Resolution:
    task_class: str
    verdicts: tuple[RungVerdict, ...]
    #: Set when ``--model`` named a model and no usable rung of the class
    #: speaks its dialect — the mismatch, stated, so the caller refuses at the
    #: prompt instead of sending a Claude id to Ollama and reading a 404.
    forced_unplaced: str = ""

    @property
    def usable(self) -> tuple[RungVerdict, ...]:
        return tuple(v for v in self.verdicts if v.usable)

    @property
    def skipped(self) -> tuple[RungVerdict, ...]:
        return tuple(v for v in self.verdicts if not v.usable)


def model_dialect(model: str) -> str:
    """Which dialect a model *name* belongs to, by the shape of the name.

    ``claude-…`` is Anthropic's; an Ollama tag carries a ``:`` and no ``/``
    (``llama3.2:3b``); everything else — ``org/model`` ids, ``llama-3.3-70b``
    — is served by the OpenAI-compatible rungs. A rung that already lists the
    model in its own ``models`` map wins regardless (``Ladder.resolve``).
    """
    if model.startswith("claude"):
        return "anthropic"
    if ":" in model and "/" not in model:
        return "ollama"
    return "openai"


@dataclass(frozen=True)
class Ladder:
    version: int
    rotate_after_days: int
    rungs: Mapping[str, Rung]
    classes: Mapping[str, tuple[str, ...]]
    path: Path | None = None
    doc: tuple[str, ...] = field(default_factory=tuple)

    def rungs_for(self, task_class: str) -> tuple[Rung, ...]:
        try:
            names = self.classes[task_class]
        except KeyError:
            known = ", ".join(sorted(self.classes))
            raise LadderError(
                f"no ladder for class {task_class!r} — classes: {known}"
            ) from None
        return tuple(self.rungs[n] for n in names)

    def verdict(
        self,
        rung: Rung,
        task_class: str,
        *,
        env: Mapping[str, str] | None = None,
        today: date | None = None,
        forced_model: str | None = None,
    ) -> RungVerdict:
        """Why this rung may or may not be tried for ``task_class``.

        Order matters and is deliberate: a rung the runtime cannot speak to
        is refused before its key is looked at; a stale rung is refused
        before its key is looked at (the operator's belief about the free
        tier has expired — a present key does not renew it); only then is a
        missing key a reason to skip. A rung with no model for the class is
        skipped last, because the other three are about the rung and this
        one is about the class — and ``forced_model`` (``--model``) answers
        that last question in the caller's favour, never the first three.
        """
        env = os.environ if env is None else env
        # UTC, not the box's wall clock: verify_at is a date the operator
        # wrote, and a staleness edge should not move with the timezone.
        today = today or datetime.now(timezone.utc).date()
        if rung.dialect not in DIALECTS:
            return RungVerdict(
                rung,
                STATUS_REFUSED,
                f"{rung.name}: dialect {rung.dialect!r} has no client here",
            )
        if rung.verify_at is not None:
            age = (today - rung.verify_at).days
            if age > self.rotate_after_days:
                return RungVerdict(
                    rung,
                    STATUS_STALE,
                    f"{rung.name}: verify_at {rung.verify_at.isoformat()} is "
                    f"{age}d old, over rotate_after_days={self.rotate_after_days} "
                    "— re-verify the tier in its console and bump verify_at",
                )
        if rung.key_env is not None and not env.get(rung.key_env):
            return RungVerdict(
                rung, STATUS_NO_KEY, f"{rung.name}: {rung.key_env} is unset"
            )
        if forced_model:
            return RungVerdict(
                rung,
                STATUS_USABLE,
                f"{rung.name}: usable (model forced: {forced_model})",
                model=forced_model,
            )
        model = rung.model_for(task_class)
        if not model:
            return RungVerdict(
                rung,
                STATUS_REFUSED,
                f"{rung.name}: no model for class {task_class!r} "
                f"(has {', '.join(sorted(rung.models)) or 'none'})",
            )
        return RungVerdict(rung, STATUS_USABLE, f"{rung.name}: usable", model=model)

    def resolve(
        self,
        task_class: str,
        *,
        env: Mapping[str, str] | None = None,
        today: date | None = None,
        model: str | None = None,
        force_dialect: str | None = None,
    ) -> Resolution:
        """The class's rungs in fall-through order, each with its verdict.

        ``model`` (``--model``) forces that model onto the first usable rung
        that can serve it — one that lists the model itself, or whose dialect
        matches the name's shape (``model_dialect``) — and leaves the rest on
        their own models. It does not make an unusable rung usable, and it
        does not land on a rung that cannot speak it: a Claude id never goes
        to Ollama. When no usable rung can serve it, every rung keeps its own
        model and ``forced_unplaced`` names the mismatch for the caller.
        ``force_dialect`` overrides the name-shape guess — ``--local`` knows
        every model it is handed is Ollama's, tag or no tag.
        """
        verdicts: list[RungVerdict] = []
        pending = model or None
        wanted = force_dialect or (model_dialect(pending) if pending else "")
        placed: int | None = None
        for rung in self.rungs_for(task_class):
            serves = bool(pending) and (
                pending in rung.models.values() or rung.dialect == wanted
            )
            v = self.verdict(
                rung,
                task_class,
                env=env,
                today=today,
                forced_model=pending if serves else None,
            )
            if serves and v.usable:
                pending = None  # forced onto this rung; the rest keep their own
                placed = len(verdicts)
            verdicts.append(v)
        if placed:
            # The operator named a model; the rung that serves it goes first
            # and the rest keep the sealed order behind it as the fall-through.
            verdicts.insert(0, verdicts.pop(placed))
        unplaced = ""
        if pending:
            usable = [v.rung for v in verdicts if v.usable]
            speaks = ", ".join(f"{r.name} ({r.dialect})" for r in usable) or "none"
            unplaced = (
                f"--model {pending}: no usable rung in class {task_class!r} speaks "
                f"its dialect ({wanted}); usable rungs: {speaks}"
            )
        return Resolution(
            task_class=task_class, verdicts=tuple(verdicts), forced_unplaced=unplaced
        )

    def doctor(
        self, *, env: Mapping[str, str] | None = None, today: date | None = None
    ) -> list[tuple[str, str, str]]:
        """Every rung's status, independent of class: (name, status, reason).

        The class-specific "no model" refusal cannot apply here, so a rung
        that is reachable and keyed reads ``usable`` even if some class has
        no model on it — the class ladder never names such a rung anyway.
        """
        rows: list[tuple[str, str, str]] = []
        for rung in self.rungs.values():
            # Any class the rung has a model for will do for the class-free
            # read; a rung with no models at all is refused by ``verdict``.
            probe_class = next(iter(rung.models), "")
            v = self.verdict(rung, probe_class, env=env, today=today)
            rows.append((rung.name, v.status, v.reason))
        return rows


def _date(value, *, where: str) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise LadderError(f"{where}: verify_at must be an ISO date or null")
    if value.startswith("pre-"):
        # ``"issued": "pre-2026-09-20"`` is an honest ``issued``; a ``verify_at``
        # must be a real date — the staleness rule cannot subtract from "pre-".
        raise LadderError(f"{where}: verify_at {value!r} is not a date")
    # Exactly YYYY-MM-DD: fromisoformat would also take "20260920", which is
    # not the shape the file promises.
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise LadderError(f"{where}: verify_at {value!r} is not YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise LadderError(f"{where}: verify_at {value!r} is not YYYY-MM-DD") from None


def _rung(name: str, raw) -> Rung:
    where = f"rungs.{name}"
    if not isinstance(raw, dict):
        raise LadderError(f"{where}: must be an object")
    for key in ("provider", "dialect", "base_url", "models"):
        if key not in raw:
            raise LadderError(f"{where}: missing {key!r}")
    if "key_env" not in raw:
        raise LadderError(
            f"{where}: missing 'key_env' — name the env var, or null for none"
        )
    key_env = raw["key_env"]
    if key_env is not None and (not isinstance(key_env, str) or not key_env):
        raise LadderError(f"{where}: key_env must be a non-empty string or null")
    # The one rule this file exists to keep: a key NAME, never a value. A
    # value has no business being a valid env-var name, and an env-var name
    # has no business looking like a secret.
    if key_env is not None and not key_env.replace("_", "").isalnum():
        raise LadderError(f"{where}: key_env {key_env!r} is not an env var name")
    models = raw["models"]
    if not isinstance(models, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and v for k, v in models.items()
    ):
        raise LadderError(f"{where}: models must map class -> model name")
    if not isinstance(raw["base_url"], str) or not raw["base_url"]:
        raise LadderError(f"{where}: base_url must be a non-empty string")
    return Rung(
        name=name,
        provider=str(raw["provider"]),
        dialect=str(raw["dialect"]),
        base_url=raw["base_url"],
        key_env=key_env,
        models=dict(models),
        verify_at=_date(raw.get("verify_at"), where=where),
        status=str(raw.get("status", "unmeasured")),
    )


def parse_ladder(data, *, path: Path | None = None) -> Ladder:
    if not isinstance(data, dict):
        raise LadderError("ladder: top level must be an object")
    version = data.get("version")
    if not isinstance(version, int) or version < 1:
        raise LadderError("ladder: 'version' must be a positive integer")
    rotate = data.get("rotate_after_days")
    if not isinstance(rotate, int) or rotate < 1:
        raise LadderError("ladder: 'rotate_after_days' must be a positive integer")
    raw_rungs = data.get("rungs")
    if not isinstance(raw_rungs, dict) or not raw_rungs:
        raise LadderError("ladder: 'rungs' must be a non-empty object")
    rungs = {name: _rung(name, raw) for name, raw in raw_rungs.items()}
    raw_classes = data.get("classes")
    if not isinstance(raw_classes, dict) or not raw_classes:
        raise LadderError("ladder: 'classes' must be a non-empty object")
    classes: dict[str, tuple[str, ...]] = {}
    for cls, names in raw_classes.items():
        if not isinstance(names, list) or not names:
            raise LadderError(f"classes.{cls}: must be a non-empty list of rung names")
        for n in names:
            if n not in rungs:
                raise LadderError(f"classes.{cls}: names unknown rung {n!r}")
            if cls not in rungs[n].models:
                # A class ladder that names a rung with no model for that
                # class is a rung that can never be tried — a hole in the
                # ladder the file's author did not see. Refuse at load, where
                # it is a one-line fix, not at the turn, where it is a
                # mystery skip.
                raise LadderError(
                    f"classes.{cls}: rung {n!r} has no models.{cls} entry"
                )
        classes[cls] = tuple(names)
    doc = data.get("_doc", [])
    return Ladder(
        version=version,
        rotate_after_days=rotate,
        rungs=rungs,
        classes=classes,
        path=path,
        doc=tuple(doc) if isinstance(doc, list) else (),
    )


def load_ladder(path: Path | None = None) -> Ladder:
    path = path or LADDER_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LadderError(f"ladder: cannot read {path}: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LadderError(f"ladder: {path} is not JSON: {exc}") from None
    return parse_ladder(data, path=path)
