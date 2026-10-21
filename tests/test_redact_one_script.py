"""ratatosk's redactor takes its patterns from the one script (flatten-2 F12,
``onescript/scrub.py``, set ``credentials``). A key glued to letters is masked;
with the one script out of reach the text is withheld, with why, never passed on.
"""

from __future__ import annotations

from ratatosk import onescript_reach, redact

AWS = "AKIA" + "Q" * 16
WHY = "no one-script at /nowhere (set ONESCRIPT_HOME)"


def test_a_key_glued_to_letters_is_masked():
    out = redact.redact(f"wrap{AWS}wrap")
    assert AWS not in out and "[REDACTED:aws_access_key_id]" in out
    assert redact.found(f"wrap{AWS}wrap") == ["aws_access_key_id"]


def test_the_journal_vendor_keys_are_masked_too():
    for key, kind in (
        ("gsk_" + "a" * 40, "groq_api_key"),
        ("hf_" + "b" * 30, "hf_token"),
        ("csk-" + "c" * 30, "cerebras_api_key"),
        ("xai-" + "d" * 30, "xai_api_key"),
    ):
        assert redact.found(key) == [kind]
        assert key not in redact.redact(f"k {key} k")


def test_out_of_reach_the_text_is_withheld_with_why(monkeypatch):
    def gone(name, home=None):
        raise onescript_reach.Unreachable(WHY)

    monkeypatch.setattr(onescript_reach, "module", gone)
    out = redact.redact("nothing secret here")
    assert out == f"[REDACTED:unreachable: {WHY}]"
    assert redact.found("nothing secret here") == ["unreachable"]
    assert redact.redact("") == "" and redact.found("") == []
