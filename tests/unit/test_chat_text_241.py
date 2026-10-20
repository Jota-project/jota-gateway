import logging

from src.services.openclaw.chat_text import ChatTextTracker, missing_tail


def test_missing_tail_policy():
    assert missing_tail("1, 2, 3,", "1, 2, 3, 4, 5") == " 4, 5"
    assert missing_tail("", "todo") == "todo"
    assert missing_tail("igual", "igual") == ""
    assert missing_tail(" Hola", "Hola mundo") == " mundo"
    assert missing_tail("abc", "xyz") == "xyz"
    assert missing_tail("algo", "") == ""


def test_diverge_warns_without_content(caplog):
    caplog.set_level(logging.DEBUG)
    t = ChatTextTracker()
    t.feed({"state": "delta", "deltaText": "PRIVADO uno"})
    out = t.feed(
        {"state": "final", "message": {"content": [{"type": "text", "text": "PRIVADO dos"}]}}
    )
    assert out == "PRIVADO dos"
    assert "diverges" in caplog.text
    assert "PRIVADO" not in caplog.text


def test_replace_empty_delta_does_not_change_emitted():
    t = ChatTextTracker()
    assert t.feed({"state": "delta", "deltaText": "1, 2, 3,"}) == "1, 2, 3,"
    assert t.feed({"state": "delta", "deltaText": "", "replace": True}) == ""
    out = t.feed(
        {"state": "final", "message": {"content": [{"type": "text", "text": "1, 2, 3, 4, 5"}]}}
    )
    assert out == " 4, 5"
