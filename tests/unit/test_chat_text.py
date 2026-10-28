"""ChatTextTracker / missing_tail (#241, #243): every tracker case lives here.

The stream (`stream_response`) and push (`deliver_push`) wiring keep two tests each, in
tests/integration/test_openclaw_final_text_241.py and tests/unit/test_bridge_push_delta_243.py.
"""

import logging

import pytest

from src.services.openclaw.chat_text import ChatTextTracker, missing_tail


def _m(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _delta(text, message=None, replace=False):
    p = {"state": "delta", "deltaText": text}
    if message is not None:
        p["message"] = _m(message)
    if replace:
        p["replace"] = True
    return p


def _final(text):
    return {"state": "final", "message": _m(text)}


# ── missing_tail ──────────────────────────────────────────────────────────────


def test_missing_tail_policy():
    assert missing_tail("1, 2, 3,", "1, 2, 3, 4, 5") == " 4, 5"
    assert missing_tail("", "todo") == "todo"
    assert missing_tail("igual", "igual") == ""
    assert missing_tail(" Hola", "Hola mundo") == " mundo"
    assert missing_tail("abc", "xyz") == "xyz"
    assert missing_tail("algo", "") == ""


def test_real_divergence_still_emits_full_with_warning(caplog):
    caplog.set_level(logging.DEBUG)
    assert missing_tail("abc", "xyz") == "xyz"
    assert "diverges" in caplog.text


def test_whitespace_only_emitted_does_not_warn(caplog):
    caplog.set_level(logging.DEBUG)
    assert missing_tail("  ", "Hola") == "Hola"
    assert "diverges" not in caplog.text
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "\n"})
    assert t.feed(_final("Hola")) == "Hola"
    assert "diverges" not in caplog.text


# ── #241: full text only in `final` ───────────────────────────────────────────


def test_final_without_any_delta_emits_full_text():
    assert ChatTextTracker().feed(_final("Hola completo")) == "Hola completo"


def test_final_with_trimmed_leading_space_emits_only_suffix():
    t = ChatTextTracker()
    assert t.feed({"state": "delta", "deltaText": " Hola"}) == " Hola"
    assert t.feed(_final("Hola mundo")) == " mundo"


def test_completely_different_final_is_emitted_in_full():
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "abc"})
    assert t.feed(_final("xyz")) == "xyz"


@pytest.mark.parametrize(
    "final",
    [
        {"state": "final"},
        {"state": "final", "message": {"role": "assistant", "content": [{"type": "image"}]}},
    ],
    ids=["no_message", "no_text_parts"],
)
def test_final_without_usable_message_emits_nothing(final):
    t = ChatTextTracker()
    assert t.feed({"state": "delta", "deltaText": "Hola"}) == "Hola"
    assert t.feed(final) == ""


def test_string_content_is_accepted():
    final = {"state": "final", "message": {"role": "assistant", "content": "texto plano"}}
    assert ChatTextTracker().feed(final) == "texto plano"


def test_diverge_warns_without_content(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "PRIVADO uno"})
    assert t.feed(_final("PRIVADO dos")) == "PRIVADO dos"
    assert "diverges" in caplog.text
    assert "PRIVADO" not in caplog.text


# ── #243: delta / replace / snapshot ──────────────────────────────────────────


def test_captured_sequence():
    t = ChatTextTracker()
    out = [
        t.feed(_delta("1, 2, 3,", "1, 2, 3,")),
        t.feed(_delta("", "", replace=True)),
        t.feed(_final("1, 2, 3, 4, 5")),
    ]
    assert out == ["1, 2, 3,", "", " 4, 5"]


def test_no_duplicate_after_empty_replace_delta_without_message_and_full_final():
    t = ChatTextTracker()
    out = [
        t.feed(_delta("1, 2, 3,", "1, 2, 3,")),
        t.feed(_delta("", "", replace=True)),
        t.feed(_delta(" 4")),
        t.feed(_final("1, 2, 3, 4, 5")),
    ]
    assert "".join(out) == "1, 2, 3, 4, 5"
    assert out == ["1, 2, 3,", "", " 4", ", 5"]


def test_replace_nonempty_does_not_emit_whole_text_as_addition(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    assert t.feed(_delta("Hola mun", "Hola mun")) == "Hola mun"
    assert t.feed(_delta("Hola, mundo", "Hola, mundo", replace=True)) == "Hola, mundo"
    assert "diverges" in caplog.text
    assert "Hola" not in caplog.text


def test_replace_extending_emitted_emits_only_suffix():
    t = ChatTextTracker()
    t.feed(_delta("Hola", "Hola"))
    assert t.feed(_delta("Hola mundo", "Hola mundo", replace=True)) == " mundo"


def test_first_frame_snapshot_includes_delta_once():
    t = ChatTextTracker()
    assert t.feed(_delta("Hola", "Hola")) == "Hola"
    assert t.feed(_delta(" mundo")) == " mundo"
    assert t.feed(_final("Hola mundo")) == ""


def test_join_mid_run_emits_whole_snapshot():
    assert ChatTextTracker().feed(_delta("mundo", "Hola mundo")) == "Hola mundo"


def test_later_delta_without_message_adds_only_addition():
    t = ChatTextTracker()
    t.feed(_delta("Hola", "Hola"))
    assert t.feed(_delta(" a")) == " a"
    assert t.feed(_delta(" todos")) == " todos"
    assert t.feed(_final("Hola a todos")) == ""


def test_snapshot_only_frame_applies_missing():
    t = ChatTextTracker()
    t.feed(_delta("Hola", "Hola"))
    assert t.feed({"state": "delta", "message": _m("Hola mundo")}) == " mundo"


def test_final_correction_diverges(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    t.feed(_delta("SECRETO uno", "SECRETO uno"))
    assert t.feed(_final("SECRETO dos")) == "SECRETO dos"
    assert "diverges" in caplog.text
    assert "SECRETO" not in caplog.text


def test_replace_empty_delta_does_not_change_emitted():
    t = ChatTextTracker()
    assert t.feed({"state": "delta", "deltaText": "1, 2, 3,"}) == "1, 2, 3,"
    assert t.feed({"state": "delta", "deltaText": "", "replace": True}) == ""
    assert t.feed(_final("1, 2, 3, 4, 5")) == " 4, 5"


def test_reset_between_responses():
    t = ChatTextTracker()
    t.feed(_delta("uno", "uno"))
    t.feed(_final("uno"))
    assert t.feed(_delta("dos", "dos")) == "dos"


def test_all_out_resets_with_tracker():
    t = ChatTextTracker()
    t.feed(_delta("uno", "uno"))
    t.reset()
    assert t.feed(_final("dos")) == "dos"


def test_aborted_and_error_do_not_emit_text_tail():
    for state in ("aborted", "error"):
        t = ChatTextTracker()
        assert t.feed({"state": "delta", "deltaText": "Hola"}) == "Hola"
        assert t.feed({"state": state, "message": _m("Hola mundo")}) == ""


# ── #243: retraction to a strict prefix of what was emitted ──────────────────


def test_final_retracted_to_prefix_emits_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    assert t.feed({"state": "delta", "deltaText": "SECRETO, mundo. Adios"}) == "SECRETO, mundo. Adios"
    assert t.feed(_final("SECRETO, mundo.")) == ""
    assert "retracted" in caplog.text
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_replace_retracted_to_prefix_emits_nothing(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    t.feed(_delta("SECRETO, mundo. Adios", "SECRETO, mundo. Adios"))
    assert t.feed(_delta("SECRETO, mundo.", "SECRETO, mundo.", replace=True)) == ""
    assert "diverges" not in caplog.text
    assert "SECRETO" not in caplog.text


def test_growth_after_retraction_emits_only_new_tail():
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "Hola mundo"})
    assert t.feed({"state": "delta", "deltaText": "Hola", "replace": True}) == ""
    assert t.feed({"state": "delta", "deltaText": " mundo!"}) == "!"
