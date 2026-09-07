from ratatosk.protocol.envelope import Intent, build_envelope, parse_grove_message, validate_envelope


def test_validate_rejects_wrong_node():
    env = build_envelope(to="other", prompt="hi", intent=Intent.CHAT.value)
    result = validate_envelope(env, node="ratatosk", check_replay=False)
    assert not result.ok


def test_parse_addressed_message():
    env = parse_grove_message({"sender": "willow", "content": "ratatosk: status please"})
    assert env is not None
    assert env.to == "ratatosk"
    assert "status" in env.prompt
