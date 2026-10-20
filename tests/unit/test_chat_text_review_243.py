"""Issue #243: hallazgos de la revision independiente."""

import logging

from src.services.openclaw.chat_text import ChatTextTracker, missing_tail


def _m(text):
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def test_no_duplicate_after_empty_replace_delta_without_message_and_full_final():
    t = ChatTextTracker()
    out = [
        t.feed({"state": "delta", "deltaText": "1, 2, 3,", "message": _m("1, 2, 3,")}),
        t.feed({"state": "delta", "deltaText": "", "replace": True, "message": _m("")}),
        t.feed({"state": "delta", "deltaText": " 4"}),
        t.feed({"state": "final", "message": _m("1, 2, 3, 4, 5")}),
    ]
    assert "".join(out) == "1, 2, 3, 4, 5"
    assert out == ["1, 2, 3,", "", " 4", ", 5"]


def test_all_out_resets_with_tracker():
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "uno", "message": _m("uno")})
    t.reset()
    assert t.feed({"state": "final", "message": _m("dos")}) == "dos"


def test_whitespace_only_emitted_does_not_warn(caplog):
    caplog.set_level(logging.DEBUG)
    assert missing_tail("  ", "Hola") == "Hola"
    assert "diverges" not in caplog.text
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "\n"})
    assert t.feed({"state": "final", "message": _m("Hola")}) == "Hola"
    assert "diverges" not in caplog.text


def test_aborted_and_error_do_not_emit_text_tail():
    for state in ("aborted", "error"):
        t = ChatTextTracker()
        assert t.feed({"state": "delta", "deltaText": "Hola"}) == "Hola"
        assert t.feed({"state": state, "message": _m("Hola mundo")}) == ""
