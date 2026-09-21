"""Walk the ladder for one completion, and ink what happened.

Sealed decision 3613d55e's second clause: *every wake inks provider, model,
rung and tokens* so "cheapest-capable" is measured, not assumed. This module
is where the inking happens — a ``TurnReceipt`` per model call, written to
the session JSONL as its own entry type and posted to Grove when a channel
is bound. A receipt is not a log line: a refused turn gets one too, with the
trail of every rung tried and why each stepped.

Fall-through rules (the brief's, verbatim in code): a ``retryable``
``ProviderError`` — 429, quota/credit, overloaded, timeout — steps to the
next rung; a non-retryable one is that rung's defect and the turn refuses
naming it; exhausting the ladder refuses listing every rung's reason.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from ratatosk import grove as _grove
from ratatosk.ladder import Ladder, Resolution, Rung, RungVerdict, load_ladder
from ratatosk.providers import (
    AnthropicClient,
    Completion,
    OllamaClient,
    OpenAICompatibleClient,
    ProviderError,
    Request,
)

UNMEASURED = "unmeasured"


@dataclass
class TrailStep:
    rung: str
    model: str | None
    kind: str
    reason: str


@dataclass
class TurnReceipt:
    """One model call, as it happened."""

    task_class: str
    outcome: str  # "ok" | "refused"
    provider: str | None = None
    model: str | None = None
    rung: str | None = None
    rung_index: int | None = None
    rung_count: int = 0
    tokens_in: int | str = UNMEASURED
    tokens_out: int | str = UNMEASURED
    latency_ms: int | None = None
    trail: list[TrailStep] = field(default_factory=list)
    skipped: list[TrailStep] = field(default_factory=list)
    reason: str = ""
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return asdict(self)

    def line(self) -> str:
        """One line for Grove and the terminal — the receipt, not a summary of it."""
        if self.outcome == "ok":
            where = f"{self.provider}/{self.model}"
            rung = (
                f"rung={self.rung_index + 1}/{self.rung_count} {self.rung}"
                if self.rung_index is not None
                else f"rung={self.rung}"
            )
            head = (
                f"[ratatosk] turn ok class={self.task_class} {rung} {where} "
                f"tokens={self.tokens_in}/{self.tokens_out} latency={self.latency_ms}ms"
            )
        else:
            head = f"[ratatosk] turn refused class={self.task_class}: {self.reason}"
        if self.trail:
            head += " trail=" + ",".join(f"{s.rung}:{s.kind}" for s in self.trail)
        if self.skipped:
            head += " skipped=" + ",".join(f"{s.rung}:{s.kind}" for s in self.skipped)
        return head


class LadderRefused(Exception):
    """No rung answered. Carries the receipt so the caller can still ink it."""

    def __init__(self, message: str, receipt: TurnReceipt):
        super().__init__(message)
        self.receipt = receipt


ClientFactory = Callable[[Rung, str], Any]


class InferenceRouter:
    """The ladder, resolved once per session, walked once per call.

    ``env`` and ``today`` are injectable so a test can resolve a ladder
    without touching the real environment or the real calendar; ``factory``
    is injectable so a test can hand back a fake client per rung and never
    open a socket.
    """

    def __init__(
        self,
        ladder: Ladder,
        task_class: str,
        *,
        model: str | None = None,
        env: Mapping[str, str] | None = None,
        today: date | None = None,
        factory: ClientFactory | None = None,
        writer=None,
        echo: Callable[[str], None] | None = None,
    ):
        self.ladder = ladder
        self.task_class = task_class
        self.forced_model = model
        self._env = os.environ if env is None else env
        self._today = today
        self._factory = factory or self._default_factory
        self._writer = writer
        self._echo = echo
        self._clients: dict[str, Any] = {}
        self.resolution: Resolution = ladder.resolve(
            task_class, env=self._env, today=today, model=model
        )
        self.current: RungVerdict | None = None
        self.last_receipt: TurnReceipt | None = None

    # -- construction ------------------------------------------------------

    @classmethod
    def from_args(
        cls,
        *,
        task_class: str,
        model: str | None,
        local: bool,
        writer=None,
        echo=None,
        ladder: Ladder | None = None,
        env: Mapping[str, str] | None = None,
    ) -> InferenceRouter:
        ladder = ladder or load_ladder()
        if local:
            # ``--local`` keeps its meaning: Ollama only. Not a class of its
            # own in the file — it is the floor of several — so the ladder is
            # narrowed here to the ollama rung for whatever class was asked,
            # and OLLAMA_MODEL keeps its old meaning as the model when
            # ``--model`` did not name one.
            ladder = _ollama_only(ladder, task_class)
            model = model or (env or os.environ).get("OLLAMA_MODEL") or None
        return cls(ladder, task_class, model=model, env=env, writer=writer, echo=echo)

    def _default_factory(self, rung: Rung, model: str) -> Any:
        if rung.dialect == "openai":
            return OpenAICompatibleClient(
                rung.base_url, self._env.get(rung.key_env or "", "")
            )
        if rung.dialect == "anthropic":
            return AnthropicClient(
                self._env.get(rung.key_env or "", ""), echo=self._echo
            )
        if rung.dialect == "ollama":
            return OllamaClient(rung.base_url)
        raise ProviderError(
            f"{rung.name}: no client for dialect {rung.dialect!r}",
            retryable=False,
            kind="bad_request",
        )

    def _client(self, verdict: RungVerdict) -> Any:
        key = verdict.rung.name
        if key not in self._clients:
            self._clients[key] = self._factory(verdict.rung, verdict.model or "")
        return self._clients[key]

    # -- the walk ----------------------------------------------------------

    def complete(self, system: str, messages, tools) -> tuple[Completion, TurnReceipt]:
        usable = self.resolution.usable
        skipped = [
            TrailStep(v.rung.name, v.model, v.status, v.reason)
            for v in self.resolution.skipped
        ]
        trail: list[TrailStep] = []
        if not usable:
            receipt = TurnReceipt(
                task_class=self.task_class,
                outcome="refused",
                rung_count=0,
                skipped=skipped,
                reason="no usable rung: " + "; ".join(s.reason for s in skipped),
            )
            self._ink(receipt)
            raise LadderRefused(receipt.reason, receipt)

        for index, verdict in enumerate(usable):
            model = verdict.model or ""
            request = Request(
                model=model, system=system, messages=messages, tools=tools
            )
            try:
                client = self._client(verdict)
                completion = client.complete(request)
            except ProviderError as exc:
                step = TrailStep(verdict.rung.name, model, exc.kind, str(exc))
                if exc.retryable:
                    trail.append(step)
                    continue
                receipt = TurnReceipt(
                    task_class=self.task_class,
                    outcome="refused",
                    provider=verdict.rung.provider,
                    model=model,
                    rung=verdict.rung.name,
                    rung_index=index,
                    rung_count=len(usable),
                    trail=trail,
                    skipped=skipped,
                    reason=f"{verdict.rung.name} ({exc.kind}): {exc}",
                )
                self._ink(receipt)
                raise LadderRefused(receipt.reason, receipt) from exc
            self.current = verdict
            receipt = TurnReceipt(
                task_class=self.task_class,
                outcome="ok",
                provider=verdict.rung.provider,
                model=completion.raw_model or model,
                rung=verdict.rung.name,
                rung_index=index,
                rung_count=len(usable),
                tokens_in=completion.tokens_in
                if completion.tokens_in is not None
                else UNMEASURED,
                tokens_out=completion.tokens_out
                if completion.tokens_out is not None
                else UNMEASURED,
                latency_ms=completion.latency_ms,
                trail=trail,
                skipped=skipped,
            )
            self._ink(receipt)
            return completion, receipt

        receipt = TurnReceipt(
            task_class=self.task_class,
            outcome="refused",
            rung_count=len(usable),
            trail=trail,
            skipped=skipped,
            reason="ladder exhausted: "
            + "; ".join(f"{s.rung} {s.kind}: {s.reason}" for s in trail),
        )
        self._ink(receipt)
        raise LadderRefused(receipt.reason, receipt)

    # -- inking ------------------------------------------------------------

    def _ink(self, receipt: TurnReceipt) -> None:
        """Write the receipt everywhere it belongs. Never raises: a receipt
        that could end the session would be a receipt nobody dares write."""
        self.last_receipt = receipt
        if self._writer is not None:
            try:
                self._writer.write_receipt(receipt.as_dict())
            except Exception as exc:
                print(f"  [receipt] not written: {exc}", flush=True)
        try:
            _grove.turn_receipt(receipt.line())
        except Exception as exc:
            print(f"  [receipt] grove post failed: {exc}", flush=True)

    # -- reporting ---------------------------------------------------------

    def doctor_rows(self) -> list[tuple[str, str, str]]:
        return self.ladder.doctor(env=self._env, today=self._today)

    def describe_current(self) -> str:
        if self.current is None:
            first = self.resolution.usable[0] if self.resolution.usable else None
            if first is None:
                return f"class={self.task_class} no usable rung"
            return f"class={self.task_class} next={first.rung.name}/{first.model} (no call yet)"
        v = self.current
        return f"class={self.task_class} rung={v.rung.name}/{v.model}"


def _ollama_only(ladder: Ladder, task_class: str) -> Ladder:
    names = tuple(
        n
        for n in ladder.classes.get(task_class, ())
        if ladder.rungs[n].dialect == "ollama"
    )
    if not names:
        names = tuple(n for n, r in ladder.rungs.items() if r.dialect == "ollama")
    return Ladder(
        version=ladder.version,
        rotate_after_days=ladder.rotate_after_days,
        rungs=ladder.rungs,
        classes={**dict(ladder.classes), task_class: names},
        path=ladder.path,
        doc=ladder.doc,
    )
