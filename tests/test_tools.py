import json

from ratatosk.tools import dispatch


def test_bash_blocked_without_trust():
    result = dispatch("Bash", {"command": "echo hi"}, set(), None, trusted=False)
    payload = json.loads(result)
    assert "error" in payload


def test_bash_allowed_with_trust():
    result = dispatch("Bash", {"command": "echo ratatosk-test"}, set(), None, trusted=True)
    assert "ratatosk-test" in str(result)
