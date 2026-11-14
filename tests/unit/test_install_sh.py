"""`install.sh` (#330): respuestas afirmativas e idioma de los mensajes.

El script es POSIX sh y se ejecuta con `curl … | sh`: aquí se carga con `FOGAR_INSTALL_LIB=1`
(define las funciones y no ejecuta `main`) bajo dash si existe, si no bajo `sh`.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"
SHELL = shutil.which("dash") or shutil.which("sh")

pytestmark = pytest.mark.skipif(SHELL is None, reason="no hay sh")


def _sh(script: str, *, source: Path = INSTALL_SH, **env: str) -> subprocess.CompletedProcess:
    full = {"PATH": "/usr/bin:/bin", "FOGAR_INSTALL_LIB": "1", **env}
    assert SHELL is not None
    return subprocess.run(
        [SHELL, "-c", f'. "{source}"\n{script}'],
        env=full,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )


def _lang(**env: str) -> str:
    r = _sh('pick_lang; printf %s "$lang"', **env)
    assert r.returncode == 0, r.stderr
    return r.stdout


def _is_yes(answer: str) -> bool:
    r = _sh('is_yes "$ANSWER"', ANSWER=answer)
    assert r.stderr == ""
    return r.returncode == 0


def _offer(tmp_path: Path, answer: str, **env: str) -> tuple[subprocess.CompletedProcess, str]:
    """Ejecuta `offer` con un «terminal» de ficheros: lee `answer` y escribe el prompt."""
    tty_in = tmp_path / "tty-in"
    tty_out = tmp_path / "tty-out"
    tty_in.write_text(answer + "\n", encoding="utf-8")
    tty_out.write_text("", encoding="utf-8")
    patched = tmp_path / "install.sh"
    text = INSTALL_SH.read_text(encoding="utf-8")
    text = text.replace("</dev/tty", f"<{tty_in}").replace(">/dev/tty", f">{tty_out}")
    patched.write_text(text, encoding="utf-8")
    r = _sh("pick_lang\nhas_tty() { return 0; }\noffer Motivo echo ran", source=patched, **env)
    return r, tty_out.read_text(encoding="utf-8")


def test_sourcing_with_the_guard_does_not_run_main():
    r = _sh("printf loaded")
    assert (r.returncode, r.stdout, r.stderr) == (0, "loaded", "")


@pytest.mark.parametrize(
    "answer", ["s", "S", "si", "SI", "Si", "sí", "Sí", "SÍ", "y", "Y", "yes", "YES", "Yes"]
)
def test_affirmative_answers_continue(answer):
    assert _is_yes(answer)


@pytest.mark.parametrize(
    "answer", ["", "n", "N", "no", "ss", "yess", "sii", "sí.", "ya", "1", "s y"]
)
def test_other_answers_abort(answer):
    assert not _is_yes(answer)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"LANG": "es_ES.UTF-8"}, "es"),
        ({"LANG": "es"}, "es"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "C"}, "en"),
        ({"LANG": "POSIX"}, "en"),
        ({}, "en"),
        ({"LANG": "en_US.UTF-8", "LC_MESSAGES": "es_ES.UTF-8"}, "es"),
        ({"LANG": "es_ES.UTF-8", "LC_MESSAGES": "en_US.UTF-8"}, "en"),
        ({"LANG": "es_ES.UTF-8", "LC_MESSAGES": "C", "LC_ALL": "en_GB.UTF-8"}, "en"),
        ({"LANG": "es_ES.UTF-8", "LC_ALL": ""}, "es"),  # vacío = sin definir
    ],
)
def test_language_follows_lc_all_then_lc_messages_then_lang(env, expected):
    assert _lang(**env) == expected


@pytest.mark.parametrize(
    ("env", "answer", "prompt_part"),
    [
        ({"LANG": "en_US.UTF-8"}, "y", "is needed; shall I run it? [y/N] "),
        ({"LANG": "es_ES.UTF-8"}, "sí", "hace falta «echo ran»; ¿lo ejecuto yo? [s/N] "),
        ({"LANG": "es_ES.UTF-8"}, "yes", "[s/N] "),
        ({"LANG": "en_US.UTF-8"}, "SI", "[y/N] "),
        ({}, "yes", "[y/N] "),
    ],
)
def test_yes_runs_the_command_with_the_prompt_in_the_language(env, answer, prompt_part, tmp_path):
    r, prompt = _offer(tmp_path, answer, **env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ran"
    assert prompt.startswith("Motivo: ")
    assert prompt.endswith(prompt_part)


@pytest.mark.parametrize(
    ("env", "answer", "msg"),
    [
        ({"LANG": "en_US.UTF-8"}, "", "launch the installer again"),
        ({"LANG": "en_US.UTF-8"}, "n", "launch the installer again"),
        ({"LANG": "es_ES.UTF-8"}, "", "vuelve a lanzar el instalador"),
        ({"LANG": "es_ES.UTF-8"}, "no", "vuelve a lanzar el instalador"),
    ],
)
def test_no_answer_aborts_without_running_the_command(env, answer, msg, tmp_path):
    r, _ = _offer(tmp_path, answer, **env)
    assert r.returncode == 1
    assert "ran" not in r.stdout
    assert msg in r.stderr


def test_t_picks_the_text_of_the_language():
    assert _sh("lang=es; t uno one", LANG="C").stdout == "uno"
    assert _sh("lang=en; t uno one", LANG="C").stdout == "one"
