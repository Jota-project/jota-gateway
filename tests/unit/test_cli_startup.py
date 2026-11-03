"""Guardas de eficiencia de `jota-gw` (M14a, #293): `--help` < 150 ms y sin imports pesados."""

import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HEAVY = ("pydantic", "httpx", "sqlmodel", "fastapi", "rich", "questionary", "prompt_toolkit")

# En un proceso nuevo: dentro de pytest el conftest ya ha cargado fastapi y pydantic.
_PROBE = f"""
import sys
from jota_gateway.cli.app import cli
import jota_gateway.cli.clients, jota_gateway.cli.status, jota_gateway.cli.version
import jota_gateway.cli.config, jota_gateway.cli.doctor, jota_gateway.cli.menu
try:
    cli(["--help"], standalone_mode=False)
except SystemExit:
    pass
print("HEAVY=" + ",".join(m for m in {HEAVY!r} if m in sys.modules))
"""


def test_cli_entry_and_commands_do_not_import_heavy_modules():
    r = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert r.stdout.splitlines()[-1] == "HEAVY=", r.stdout.splitlines()[-1]


def test_help_starts_under_150_ms():
    def once() -> float:
        start = time.perf_counter()
        subprocess.run(
            [sys.executable, "-m", "jota_gateway.cli", "--help"], cwd=ROOT, capture_output=True, check=True
        )
        return time.perf_counter() - start

    once()  # calienta la caché de bytecode
    median = statistics.median(once() for _ in range(5))
    assert median < 0.150, f"mediana {median * 1000:.0f} ms"
