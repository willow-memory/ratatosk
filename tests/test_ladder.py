"""The ladder is data, read and validated — never guessed past."""

import json
from datetime import date

import pytest

from ratatosk import ladder as _ladder
from ratatosk.ladder import (
    STATUS_NO_KEY,
    STATUS_REFUSED,
    STATUS_STALE,
    STATUS_USABLE,
    LadderError,
    load_ladder,
    parse_ladder,
)

TODAY = date(2026, 9, 21)


def _good() -> dict:
    return {
        "version": 1,
        "rotate_after_days": 90,
        "rungs": {
            "free": {
                "provider": "free-inc",
                "dialect": "openai",
                "base_url": "https://free.example/v1",
                "key_env": "FREE_API_KEY",
                "models": {"build": "free-70b", "chat": "free-8b"},
                "verify_at": "2026-09-20",
            },
            "floor": {
                "provider": "ollama",
                "dialect": "ollama",
                "base_url": "http://127.0.0.1:11434",
                "key_env": None,
                "models": {"chat": "llama3.2:3b"},
                "verify_at": None,
            },
            "paid": {
                "provider": "anthropic",
                "dialect": "anthropic",
                "base_url": "https://api.anthropic.com",
                "key_env": "ANTHROPIC_API_KEY",
                "models": {"build": "claude-sonnet-5", "audit": "claude-opus-5"},
                "verify_at": None,
            },
        },
        "classes": {
            "build": ["free", "paid"],
            "chat": ["floor", "free"],
            "audit": ["paid"],
        },
    }


def test_the_shipped_ladder_loads_and_names_every_class_rung():
    """The file beside the module is the one crown reads; it must parse and
    every rung a class names must carry a model for that class."""
    ladder = load_ladder()
    assert ladder.path == _ladder.LADDER_PATH
    assert set(ladder.classes) >= {
        "build",
        "audit",
        "research",
        "operate",
        "witness",
        "chat",
        "summarize",
        "classify",
    }
    for cls, names in ladder.classes.items():
        for n in names:
            assert cls in ladder.rungs[n].models, f"{cls} names {n} with no model"


def test_the_shipped_ladder_holds_key_names_not_values():
    ladder = load_ladder()
    for rung in ladder.rungs.values():
        if rung.key_env is not None:
            assert rung.key_env.isupper() and rung.key_env.endswith("_KEY"), (
                rung.key_env
            )


def test_a_good_file_resolves_a_class_in_order():
    ladder = parse_ladder(_good())
    res = ladder.resolve(
        "build", env={"FREE_API_KEY": "x", "ANTHROPIC_API_KEY": "y"}, today=TODAY
    )
    assert [v.rung.name for v in res.usable] == ["free", "paid"]
    assert [v.model for v in res.usable] == ["free-70b", "claude-sonnet-5"]
    assert res.skipped == ()


def test_a_missing_class_is_refused_by_name():
    ladder = parse_ladder(_good())
    with pytest.raises(LadderError, match="no ladder for class 'witness'"):
        ladder.rungs_for("witness")


def test_a_stale_verify_at_is_refused_with_the_reason_even_when_keyed():
    """The operator's belief about the free tier has expired; a present key
    does not renew it."""
    ladder = parse_ladder(_good())
    res = ladder.resolve(
        "build",
        env={"FREE_API_KEY": "x", "ANTHROPIC_API_KEY": "y"},
        today=date(2027, 1, 1),
    )
    free, paid = res.verdicts
    assert free.status == STATUS_STALE
    assert "rotate_after_days=90" in free.reason
    assert "2026-09-20" in free.reason
    assert paid.usable
    assert [v.rung.name for v in res.usable] == ["paid"]


def test_an_unset_key_is_skipped_with_the_reason():
    ladder = parse_ladder(_good())
    res = ladder.resolve("build", env={"ANTHROPIC_API_KEY": "y"}, today=TODAY)
    free, paid = res.verdicts
    assert free.status == STATUS_NO_KEY
    assert free.reason == "free: FREE_API_KEY is unset"
    assert paid.usable


def test_ollama_needs_no_key():
    ladder = parse_ladder(_good())
    res = ladder.resolve("chat", env={}, today=TODAY)
    floor, free = res.verdicts
    assert floor.status == STATUS_USABLE
    assert free.status == STATUS_NO_KEY


def test_a_forced_model_lands_on_the_first_usable_rung_only():
    ladder = parse_ladder(_good())
    res = ladder.resolve(
        "build",
        env={"ANTHROPIC_API_KEY": "y"},  # free has no key; paid is first usable
        today=TODAY,
        model="claude-opus-5",
    )
    paid, free = res.verdicts
    assert free.status == STATUS_NO_KEY, (
        "forcing a model does not make a keyless rung usable"
    )
    assert paid.model == "claude-opus-5"
    assert "model forced" in paid.reason
    assert res.forced_unplaced == ""


def test_a_forced_model_goes_to_a_rung_that_speaks_its_dialect_and_leads():
    """Loki finding 3: `--model claude-…` on a ladder whose first usable rung
    is Ollama must not send a Claude id to Ollama. The rung that serves it
    moves to the head; the others keep the sealed order as fall-through."""
    ladder = parse_ladder(_good())
    res = ladder.resolve(
        "build",
        env={"FREE_API_KEY": "x", "ANTHROPIC_API_KEY": "y"},
        today=TODAY,
        model="claude-opus-5",
    )
    assert [(v.rung.name, v.model) for v in res.usable] == [
        ("paid", "claude-opus-5"),
        ("free", "free-70b"),
    ]


def test_a_forced_model_no_usable_rung_can_serve_is_named_not_sent():
    ladder = parse_ladder(_good())
    res = ladder.resolve(
        "chat", env={"FREE_API_KEY": "x"}, today=TODAY, model="claude-opus-5"
    )
    assert [v.model for v in res.usable] == ["llama3.2:3b", "free-8b"], (
        "every rung keeps its own model; nothing is forced onto the wrong dialect"
    )
    assert "claude-opus-5" in res.forced_unplaced
    assert "anthropic" in res.forced_unplaced
    assert "floor (ollama)" in res.forced_unplaced
    assert "free (openai)" in res.forced_unplaced


def test_a_forced_model_a_rung_already_lists_wins_over_the_name_shape():
    """`free-8b` has no ':' and no 'claude', so by shape it is openai — and
    the free rung lists it, so it lands there even on the chat ladder where
    the floor comes first."""
    ladder = parse_ladder(_good())
    res = ladder.resolve(
        "chat", env={"FREE_API_KEY": "x"}, today=TODAY, model="free-8b"
    )
    assert res.usable[0].rung.name == "free" and res.usable[0].model == "free-8b"


def _two_openai_rungs() -> dict:
    """Two usable openai rungs in class order; only the SECOND lists the
    forced model. Gap 72168ff799de's shape: cerebras then gemini on `build`,
    `--model gemini-2.5-flash`."""
    raw = _good()
    raw["rungs"]["first"] = {
        "provider": "cerebras",
        "dialect": "openai",
        "base_url": "https://first.example/v1",
        "key_env": "FIRST_API_KEY",
        "models": {"build": "first-70b"},
        "verify_at": "2026-09-20",
    }
    raw["rungs"]["second"] = {
        "provider": "google-ai-studio",
        "dialect": "openai",
        "base_url": "https://second.example/v1",
        "key_env": "SECOND_API_KEY",
        "models": {"build": "second-flash"},
        "verify_at": "2026-09-20",
    }
    raw["classes"]["build"] = ["first", "second", "paid"]
    return raw


def test_a_forced_model_prefers_the_rung_that_lists_it_over_an_earlier_same_dialect_rung():
    """Gap 72168ff799de (Loki, 4177ABB8): `--class build --model gemini-2.5-flash`
    used to land on cerebras — the first openai rung in class order — and
    404 loudly. The rung that lists the model wins; the earlier rung keeps
    its own model behind it as fall-through."""
    ladder = parse_ladder(_two_openai_rungs())
    res = ladder.resolve(
        "build",
        env={"FIRST_API_KEY": "a", "SECOND_API_KEY": "b"},
        today=TODAY,
        model="second-flash",
    )
    assert [(v.rung.name, v.model) for v in res.usable] == [
        ("second", "second-flash"),
        ("first", "first-70b"),
    ]
    assert res.forced_unplaced == ""


def test_a_forced_model_no_rung_lists_falls_to_the_first_same_dialect_rung():
    """A model nobody lists but whose shape is openai still lands on the
    first usable openai rung — the prior behaviour, kept as the fallback."""
    ladder = parse_ladder(_two_openai_rungs())
    res = ladder.resolve(
        "build",
        env={"FIRST_API_KEY": "a", "SECOND_API_KEY": "b"},
        today=TODAY,
        model="some-other-openai-model",
    )
    assert res.usable[0].rung.name == "first"
    assert res.usable[0].model == "some-other-openai-model"


def test_a_listing_rung_without_a_key_does_not_capture_the_forced_model():
    """The listing rung must be USABLE to win; unkeyed, the forced model
    falls through to the first usable same-dialect rung."""
    ladder = parse_ladder(_two_openai_rungs())
    res = ladder.resolve(
        "build", env={"FIRST_API_KEY": "a"}, today=TODAY, model="second-flash"
    )
    assert res.usable[0].rung.name == "first"
    assert res.usable[0].model == "second-flash"
    assert res.forced_unplaced == ""


def test_force_dialect_overrides_the_name_shape():
    ladder = parse_ladder(_good())
    res = ladder.resolve(
        "chat", env={}, today=TODAY, model="llama3.2", force_dialect="ollama"
    )
    assert res.usable[0].rung.name == "floor" and res.usable[0].model == "llama3.2"
    assert res.forced_unplaced == ""


@pytest.mark.parametrize(
    ("model", "dialect"),
    [
        ("claude-sonnet-5", "anthropic"),
        ("claude-opus-4-1", "anthropic"),
        ("llama3.2:3b", "ollama"),
        ("llama-3.3-70b-versatile", "openai"),
        ("meta-llama/llama-3.3-70b-instruct:free", "openai"),
        ("gemini-2.5-flash", "openai"),
    ],
)
def test_model_dialect_by_name_shape(model, dialect):
    assert _ladder.model_dialect(model) == dialect


def test_an_unknown_dialect_is_refused_not_tried():
    data = _good()
    data["rungs"]["free"]["dialect"] = "carrier-pigeon"
    ladder = parse_ladder(data)
    v = ladder.verdict(
        ladder.rungs["free"], "build", env={"FREE_API_KEY": "x"}, today=TODAY
    )
    assert v.status == STATUS_REFUSED
    assert "carrier-pigeon" in v.reason


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda d: d.pop("version"), "'version'"),
        (lambda d: d.update(rotate_after_days=0), "'rotate_after_days'"),
        (lambda d: d.update(rungs={}), "'rungs'"),
        (lambda d: d["rungs"]["free"].pop("key_env"), "missing 'key_env'"),
        (
            lambda d: d["rungs"]["free"].update(key_env="sk-live-abc"),
            "not an env var name",
        ),
        (lambda d: d["rungs"]["free"].update(verify_at="yesterday"), "not YYYY-MM-DD"),
        (
            lambda d: d["rungs"]["free"].update(verify_at="pre-2026-09-20"),
            "is not a date",
        ),
        (lambda d: d["classes"].update(build=["nope"]), "unknown rung 'nope'"),
        (lambda d: d["classes"].update(audit=["free"]), "no models.audit entry"),
        (lambda d: d["classes"].update(build=[]), "non-empty list"),
    ],
)
def test_a_malformed_file_is_refused_naming_the_field(mutate, match):
    data = _good()
    mutate(data)
    with pytest.raises(LadderError, match=match):
        parse_ladder(data)


def test_load_ladder_refuses_a_missing_or_non_json_file(tmp_path):
    with pytest.raises(LadderError, match="cannot read"):
        load_ladder(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(LadderError, match="not JSON"):
        load_ladder(bad)


def test_doctor_reports_every_rung_with_one_word_and_a_reason(tmp_path):
    ladder = parse_ladder(_good())
    rows = ladder.doctor(env={"ANTHROPIC_API_KEY": "y"}, today=TODAY)
    assert [(n, s) for n, s, _ in rows] == [
        ("free", STATUS_NO_KEY),
        ("floor", STATUS_USABLE),
        ("paid", STATUS_USABLE),
    ]
    assert all(reason for _, _, reason in rows)


def test_loading_the_file_round_trips_through_json(tmp_path):
    path = tmp_path / "ladder.json"
    path.write_text(json.dumps(_good()), encoding="utf-8")
    ladder = load_ladder(path)
    assert ladder.path == path
    assert ladder.rungs["free"].verify_at == date(2026, 9, 20)
