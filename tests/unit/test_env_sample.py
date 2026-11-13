"""`.env.sample` debe reflejar `Settings` exactamente (issue #118).

Cada campo de Settings aparece (activo o comentado si es opcional) y no queda
ninguna variable que Settings ya no conozca (p. ej. las de la antigua BD externa de clientes).
"""

import re
from pathlib import Path

from fogar_gateway.core.config import Settings

ENV_SAMPLE = Path(__file__).resolve().parents[2] / ".env.sample"
_VAR = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=")


def _documented_vars() -> set[str]:
    return {
        m.group(1)
        for line in ENV_SAMPLE.read_text(encoding="utf-8").splitlines()
        if (m := _VAR.match(line))
    }


def test_every_settings_field_is_documented_in_env_sample():
    missing = set(Settings.model_fields) - _documented_vars()
    assert missing == set(), f".env.sample no documenta: {sorted(missing)}"


def test_env_sample_has_no_variables_unknown_to_settings():
    stale = _documented_vars() - set(Settings.model_fields)
    assert stale == set(), f".env.sample documenta variables inexistentes: {sorted(stale)}"


def test_env_sample_defaults_match_settings_defaults():
    """Una línea activa (sin `#`) debe valer lo mismo que el default de Settings,
    salvo secretos, que llevan un placeholder."""
    secrets = {"ADMIN_TOKEN", "OPENCLAW_TOKEN"}
    mismatches = []
    for line in ENV_SAMPLE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if not m or m.group(1) in secrets:
            continue
        name, raw = m.group(1), m.group(2).split("#", 1)[0].strip()
        default = Settings.model_fields[name].default
        if str(default).lower() != raw.lower():
            mismatches.append(f"{name}: sample={raw!r} default={default!r}")
    assert mismatches == [], mismatches


def test_retired_key_in_config_env_is_ignored_with_one_warning(caplog):
    """#272: an old config.env still carrying ORCHESTRATOR_RECONNECT_MAX_DURATION must load."""
    import logging

    import pytest
    from pydantic import ValidationError

    caplog.set_level(logging.WARNING, logger="fogar_gateway.core.config")
    s = Settings(ORCHESTRATOR_RECONNECT_MAX_DURATION="300.0", OPENCLAW_PORT=1234)  # type: ignore[call-arg]
    assert s.OPENCLAW_PORT == 1234
    assert not hasattr(s, "ORCHESTRATOR_RECONNECT_MAX_DURATION")
    warnings = [r for r in caplog.records if "obsolete" in r.getMessage()]
    assert len(warnings) == 1

    with pytest.raises(ValidationError):  # extra='forbid' stays for everything else
        Settings(SOME_UNKNOWN_KEY="x")  # type: ignore[call-arg]
