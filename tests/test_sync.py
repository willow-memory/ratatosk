from ratatosk.paths import ratatosk_data_root
from ratatosk.session import SessionWriter
from ratatosk.sync import write_deposit


def _deposit_carries(deposit, text: str) -> bool:
    """Is `text` anywhere in the deposit on disk? Factored out of the test
    below so it can be planted (`tests/test_scans_fire.py`)."""
    return text in deposit.read_text(encoding="utf-8")


def test_deposit_writes_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "ratatosk" / "sessions" / "proj"))
    writer = SessionWriter()
    writer.write_user("hello")
    deposit = write_deposit(writer)
    assert deposit.exists()
    assert _deposit_carries(deposit, "hello")


def test_the_deposit_check_catches_a_planted_deposit_missing_the_turn(tmp_path):
    """Planted: a deposit written without the user's turn in it. The check
    must report the absence, or the test above is only asserting that a
    file exists."""
    empty = tmp_path / "deposit.md"
    empty.write_text("# deposit\n\nnothing recorded\n", encoding="utf-8")
    assert not _deposit_carries(empty, "hello")
    full = tmp_path / "full.md"
    full.write_text("# deposit\n\nuser: hello\n", encoding="utf-8")
    assert _deposit_carries(full, "hello")
