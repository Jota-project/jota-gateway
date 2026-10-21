import json
import logging
import sys
from datetime import datetime

import pytest

from src.core.logging import HealthzAccessFilter, JsonFormatter, configure_logging

_NAMES = ("", "uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture(autouse=True)
def _restore_logging():
    saved = []
    for n in _NAMES:
        lg = logging.getLogger(n)
        saved.append((lg, lg.level, lg.propagate, list(lg.handlers), list(lg.filters)))
    yield
    for lg, level, prop, handlers, filters in saved:
        lg.setLevel(level)
        lg.propagate = prop
        lg.handlers[:] = handlers
        lg.filters[:] = filters


def _record(**extra):
    r = logging.LogRecord("t", logging.INFO, __file__, 1, "hola %s", ("x",), None)
    r.__dict__.update(extra)
    return r


def test_json_formatter_emits_one_valid_json_line():
    meta = {"text_len": 3, "when": datetime(2026, 1, 1), "tags": {"a"}}
    line = JsonFormatter().format(_record(session_id="s1", meta=meta))
    d = json.loads(line)
    assert d["level"] == "INFO" and d["logger"] == "t" and d["msg"] == "hola x"
    assert d["ts"] and d["session_id"] == "s1" and d["meta"]["text_len"] == 3
    assert "\n" not in line


def test_json_formatter_includes_exception_in_single_line():
    try:
        raise ValueError("boom")
    except ValueError:
        r = _record()
        r.exc_info = sys.exc_info()
    line = JsonFormatter().format(r)
    assert "ValueError" in json.loads(line)["exc"]
    assert "\n" not in line


def test_healthz_filter_drops_only_healthz():
    f = HealthzAccessFilter()

    def rec(path):
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", "GET", path, "1.1", 200), None,
        )

    assert f.filter(rec("/healthz")) is False
    assert f.filter(rec("/ready")) is True


def test_configure_logging_json_reformats_uvicorn_loggers():
    for n in _NAMES[1:]:
        logging.getLogger(n).addHandler(logging.StreamHandler())
    configure_logging("json")
    for n in _NAMES:
        handlers = logging.getLogger(n).handlers
        assert handlers
        assert all(isinstance(h.formatter, JsonFormatter) for h in handlers)
    assert any(isinstance(f, HealthzAccessFilter) for f in logging.getLogger("uvicorn.access").filters)


def test_configure_logging_text_keeps_current_format():
    configure_logging("text")
    fmt = logging.getLogger().handlers[0].formatter
    assert fmt._fmt == "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
