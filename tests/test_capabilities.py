from ratatosk.capabilities import ActionResult, CapabilityGate
from ratatosk.protocol.envelope import Intent, build_envelope


def test_shell_without_confirm_is_rejected():
    gate = CapabilityGate()
    env = build_envelope(
        to="local", prompt="rm -rf /", intent=Intent.SHELL.value, requires_confirm=False
    )
    assert gate.classify(env) == ActionResult.REJECTED


def test_shell_with_confirm_is_queued():
    gate = CapabilityGate()
    env = build_envelope(
        to="local", prompt="ls", intent=Intent.SHELL.value, requires_confirm=True
    )
    assert gate.classify(env) == ActionResult.QUEUED_CONFIRM


def test_chat_executes():
    gate = CapabilityGate()
    env = build_envelope(to="local", prompt="hello", intent=Intent.CHAT.value)
    assert gate.classify(env) == ActionResult.EXECUTED


def test_wake_executes_without_confirmation():
    """A wake is the fleet's own activation signal, not a remote command —
    gating it behind confirmation like RUN_TASK would mean no seat could ever
    wake unattended."""
    gate = CapabilityGate()
    env = build_envelope(
        to="local", prompt="", intent=Intent.WAKE.value, capabilities=[]
    )
    assert gate.classify(env) == ActionResult.EXECUTED
