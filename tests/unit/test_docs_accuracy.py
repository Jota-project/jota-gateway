"""Guardas anti-deriva para la documentación (issues #119, #120, #121).

Fallan si un markdown rastreado vuelve a referenciar algo que ya no existe o a
describir un comportamiento que el código contradice.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# Históricos: citan nombres viejos a propósito (títulos de issues, planes).
_HISTORICAL = ("docs/ROADMAP.md", "docs/superpowers/", "AUDIT_")


def _tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    paths = [p for p in out.split("\0") if p and (ROOT / p).exists()]
    return [ROOT / p for p in paths if not any(h in p for h in _HISTORICAL)]


def _hits(pattern: str) -> list[str]:
    regex = re.compile(pattern, re.IGNORECASE)
    found = []
    for path in _tracked_markdown():
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if regex.search(line):
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:80]}")
    return found


@pytest.mark.parametrize(
    "pattern, why",
    [
        (r"create_db_and_tables", "renombrada a run_migrations() en v1.12.0 (#121)"),
        (r"sin auth|LAN-only", "/v1/* exige origen de confianza o Bearer desde #52 (#119)"),
        (r"openclaw_routes", "ese módulo nunca existió en este repo (#120)"),
    ],
)
def test_no_stale_references_in_markdown(pattern, why):
    assert _hits(pattern) == [], why


def test_openclaw_skill_index_points_only_to_existing_files():
    skill = ROOT / "docs/skills/openclaw/SKILL.md"
    refs = re.findall(r"`(references/[\w.-]+\.md)`", skill.read_text(encoding="utf-8"))
    missing = [r for r in refs if not (skill.parent / r).exists()]
    assert missing == [], f"SKILL.md referencia archivos inexistentes: {missing}"


def test_openclaw_skill_names_gateway_protocol_doc_as_authoritative():
    skill = (ROOT / "docs/skills/openclaw/SKILL.md").read_text(encoding="utf-8")
    assert "docs/openclaw-protocol.md" in skill
