"""`services/turn_progress.py` (#254): vocabulario, traducción de OpenClaw y ritmo por turno."""

import pytest

from fogar_gateway.services.openclaw.models import ToolCallEvent
from fogar_gateway.services.turn_progress import (
    Progress,
    TurnProgress,
    chat_status_phase,
    safe_tool_name,
    status_phase,
)

# ChatRunStartupPhaseSchema de OpenClaw 2026.10.5, en su orden.
OPENCLAW_PHASES = {
    "waiting_for_state": "preparing",
    "preparing_workspace": "preparing",
    "naming_worktree": "preparing",
    "creating_worktree": "preparing",
    "running_setup": "preparing",
    "provisioning_environment": "preparing",
    "preparing_context": "preparing",
    "memory_flushing": "preparing",
    "starting_model": "thinking",
}


@pytest.mark.parametrize(("phase", "expected"), OPENCLAW_PHASES.items())
def test_every_openclaw_startup_phase_maps_to_ours(phase, expected):
    assert chat_status_phase({"state": "status", "phase": phase}) == expected


@pytest.mark.parametrize("phase", ["teleporting", "", None, 42, "Bad Phase!"])
def test_an_unknown_startup_phase_is_preparing_never_the_raw_string(phase):
    assert chat_status_phase({"state": "status", "phase": phase}) == "preparing"


@pytest.mark.parametrize("state", ["delta", "final", "aborted", "error", None])
def test_only_status_frames_are_progress(state):
    assert chat_status_phase({"state": state, "phase": "starting_model"}) is None


def test_a_retry_arrives_as_starting_model_and_is_thinking():
    payload = {"state": "status", "phase": "starting_model",
               "retry": {"attempt": 2, "maxAttempts": 3, "reason": "rate_limit"}}
    assert chat_status_phase(payload) == "thinking"


def test_status_phase_accepts_only_our_status_phases():
    assert status_phase("preparing") == "preparing"
    assert status_phase("thinking") == "thinking"
    assert status_phase("tool") is None  # la herramienta sale de `tool_call`, con su nombre
    assert status_phase("preparing_workspace") is None


@pytest.mark.parametrize(
    "name", ["exec", "ha_call_service", "mcp__home-assistant__HassTurnOn", "a.b:c", "x" * 128]
)
def test_identifier_tool_names_are_sent(name):
    assert safe_tool_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "x" * 129, "rm -rf /", "名前", "a\nb", "<script>", None, 7, {"n": 1}]
)
def test_anything_else_is_not(name):
    assert safe_tool_name(name) is None


def test_the_message_has_a_fixed_shape():
    assert Progress("tool", "exec").message("t-3") == {
        "type": "turn_progress", "turn_id": "t-3", "phase": "tool", "tool": "exec"
    }
    assert Progress("thinking").message("t-3")["tool"] is None


def _start(call_id, name):
    return ToolCallEvent(phase="start", name=name, tool_call_id=call_id, args={"secret": 1})


def _result(call_id, name):
    return ToolCallEvent(phase="result", name=name, tool_call_id=call_id, result="secret")


def test_tool_start_and_result():
    p = TurnProgress(min_interval=0)
    assert p.tool(_start("c1", "read")) == Progress("tool", "read")
    assert p.tool(_result("c1", "read")) == Progress("thinking")


def test_parallel_tools_keep_showing_the_one_still_running():
    p = TurnProgress(min_interval=0)
    p.tool(_start("c1", "read"))
    p.tool(_start("c2", "exec"))
    assert p.tool(_result("c2", "exec")) == Progress("tool", "read")
    assert p.tool(_result("c1", "read")) == Progress("thinking")


def test_unsafe_tool_name_is_a_tool_without_name():
    p = TurnProgress(min_interval=0)
    assert p.tool(_start("c1", "rm -rf /")) == Progress("tool", None)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def test_the_same_state_is_never_sent_twice_in_a_row():
    p = TurnProgress(min_interval=0)
    assert p.offer(Progress("preparing")) == Progress("preparing")
    assert p.offer(Progress("preparing")) is None
    assert p.offer(Progress("thinking")) == Progress("thinking")
    assert p.offer(Progress("preparing")) == Progress("preparing")


def test_too_soon_is_deferred_and_only_the_latest_goes_out():
    clock = Clock()
    p = TurnProgress(min_interval=0.25, clock=clock)
    assert p.offer(Progress("preparing")) == Progress("preparing")
    clock.now += 0.05
    assert p.offer(Progress("thinking")) == pytest.approx(0.2)
    assert p.offer(Progress("tool", "read")) == pytest.approx(0.2)
    assert p.offer(Progress("thinking")) == pytest.approx(0.2)
    clock.now += 0.1
    assert p.flush() == pytest.approx(0.1)  # aún pronto: sigue pendiente
    clock.now += 0.15
    assert p.flush() == Progress("thinking")
    assert p.flush() is None


def test_a_deferred_state_that_returns_to_the_sent_one_is_dropped():
    clock = Clock()
    p = TurnProgress(min_interval=0.25, clock=clock)
    p.offer(Progress("thinking"))
    p.offer(Progress("tool", "read"))  # aplazado
    assert p.offer(Progress("thinking")) is None  # vuelve a lo que el cliente ya ve
    clock.now += 1
    assert p.flush() is None


def test_text_drops_what_was_deferred_and_forgets_the_last_sent():
    clock = Clock()
    p = TurnProgress(min_interval=0.25, clock=clock)
    p.offer(Progress("tool", "read"))
    p.offer(Progress("thinking"))  # aplazado
    p.text_arrived()  # salió un token: lo aplazado ya es viejo
    clock.now += 1
    assert p.flush() is None
    # El cliente borró el progreso con el token: la misma herramienta otra vez sí sale.
    assert p.offer(Progress("tool", "read")) == Progress("tool", "read")


def test_after_the_interval_a_change_goes_out_at_once():
    clock = Clock()
    p = TurnProgress(min_interval=0.25, clock=clock)
    p.offer(Progress("preparing"))
    clock.now += 0.3
    assert p.offer(Progress("thinking")) == Progress("thinking")
