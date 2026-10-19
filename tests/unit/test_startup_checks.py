"""M6f (#218): avisos de tokens por defecto y guard de un solo proceso (flock)."""

import logging
import subprocess
import sys
import textwrap

import pytest

from src.core.config import Settings
from src.core.startup_checks import (
    LOCK_FILENAME,
    SingleProcessLockError,
    acquire_single_process_lock,
    release_single_process_lock,
    warn_insecure_tokens,
)

SECRET = "s3cr3t-value-that-must-never-be-logged"


def _settings(**kw) -> Settings:
    base = {"ADMIN_TOKEN": SECRET, "OPENCLAW_TOKEN": SECRET, "TTS_TOKEN": SECRET}
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_no_warning_when_tokens_are_set(caplog):
    caplog.set_level(logging.WARNING)
    assert warn_insecure_tokens(_settings()) == []
    assert caplog.records == []


@pytest.mark.parametrize("value", ["", "changeme", "CHANGEME", " changeme ", "secret", "admin"])
def test_admin_token_empty_or_placeholder_warns(value, caplog):
    caplog.set_level(logging.WARNING)
    assert warn_insecure_tokens(_settings(ADMIN_TOKEN=value)) == ["ADMIN_TOKEN"]
    assert any("ADMIN_TOKEN" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("value", ["", "changeme"])
def test_openclaw_token_empty_or_placeholder_warns(value, caplog):
    caplog.set_level(logging.WARNING)
    assert warn_insecure_tokens(_settings(OPENCLAW_TOKEN=value)) == ["OPENCLAW_TOKEN"]


def test_tts_token_default_warns(caplog):
    caplog.set_level(logging.WARNING)
    assert warn_insecure_tokens(_settings(TTS_TOKEN="gateway")) == ["TTS_TOKEN"]


def test_warnings_never_contain_the_token_value(caplog):
    caplog.set_level(logging.DEBUG)
    warn_insecure_tokens(_settings(ADMIN_TOKEN="changeme", OPENCLAW_TOKEN="changeme"))
    warn_insecure_tokens(_settings(TTS_TOKEN="gateway"))
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "changeme" not in text.lower()
    assert "gateway" not in text.lower()


def test_lock_is_exclusive_within_the_same_process_and_released(tmp_path):
    url = f"sqlite:///{tmp_path}/gateway.db"
    first = acquire_single_process_lock(url)
    assert first is not None
    assert (tmp_path / LOCK_FILENAME).exists()
    with pytest.raises(SingleProcessLockError, match="multi-worker"):
        acquire_single_process_lock(url)
    release_single_process_lock(first)
    again = acquire_single_process_lock(url)
    assert again is not None
    release_single_process_lock(again)


def test_lock_conflicts_with_another_real_process(tmp_path):
    url = f"sqlite:///{tmp_path}/gateway.db"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys, time
                sys.path.insert(0, {str(__import__('os').getcwd())!r})
                from src.core.startup_checks import acquire_single_process_lock
                h = acquire_single_process_lock({url!r})
                print("held", flush=True)
                time.sleep(30)
                """
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(SingleProcessLockError):
            acquire_single_process_lock(url)
    finally:
        holder.kill()
        holder.wait()
    # Al morir el proceso, el SO libera el lock.
    handle = acquire_single_process_lock(url)
    assert handle is not None
    release_single_process_lock(handle)


@pytest.mark.parametrize(
    "url", ["sqlite://", "sqlite:///:memory:", "postgresql://u:p@localhost/db"]
)
def test_lock_is_skipped_for_memory_and_non_sqlite(url):
    assert acquire_single_process_lock(url) is None
