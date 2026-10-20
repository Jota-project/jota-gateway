"""Issue #243: semantica oficial delta/replace/snapshot de OpenClaw 2026.10.5."""

import logging

from src.services.openclaw.chat_text import ChatTextTracker


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


def test_captured_sequence():
    t = ChatTextTracker()
    out = [
        t.feed(_delta("1, 2, 3,", "1, 2, 3,")),
        t.feed(_delta("", "", replace=True)),
        t.feed(_final("1, 2, 3, 4, 5")),
    ]
    assert out == ["1, 2, 3,", "", " 4, 5"]


def test_replace_nonempty_does_not_emit_whole_text_as_addition(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    assert t.feed(_delta("Hola mun", "Hola mun")) == "Hola mun"
    out = t.feed(_delta("Hola, mundo", "Hola, mundo", replace=True))
    assert out == "Hola, mundo"  # divergencia: texto completo
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
    t = ChatTextTracker()
    assert t.feed(_delta("mundo", "Hola mundo")) == "Hola mundo"


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


def test_reset_between_responses():
    t = ChatTextTracker()
    t.feed(_delta("uno", "uno"))
    t.feed(_final("uno"))
    assert t.feed(_delta("dos", "dos")) == "dos"
