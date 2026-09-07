from ratatosk.paths import ratatosk_data_root
from ratatosk.session import SessionWriter
from ratatosk.sync import write_deposit


def test_deposit_writes_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("WILLOW_HOME", str(tmp_path))
    monkeypatch.setenv("RATATOSK_SESSION_DIR", str(tmp_path / "ratatosk" / "sessions" / "proj"))
    writer = SessionWriter()
    writer.write_user("hello")
    deposit = write_deposit(writer)
    assert deposit.exists()
    assert "hello" in deposit.read_text(encoding="utf-8")
