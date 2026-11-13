"""`fogar-gw access setup`: el paso «¿Cómo vas a entrar a Fogar?».

Nunca toca Tailscale ni sudo de verdad: `tailscale_status`, `serve_status`, `enable_serve` y
`subprocess.run` van parcheados; las preguntas de questionary salen de una cola.
"""

from types import SimpleNamespace

import pytest
import questionary
from click.testing import CliRunner
from dotenv import dotenv_values

from fogar_gateway import access as access_mod
from fogar_gateway.cli import access as cli_access
from fogar_gateway.cli import app, output

ADMIN = "admin-token-largo-0123456789"
DNS = "casa.tail1234.ts.net"
RUNNING = {"BackendState": "Running", "Self": {"DNSName": f"{DNS}."}}
OURS = {"TCP": {"443": {"HTTPS": True}},
        "Web": {f"{DNS}:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8004"}}}}}


@pytest.fixture()
def st(monkeypatch, tmp_path):
    """Base con config.env, Tailscale en marcha sin serve y una terminal de mentira."""
    monkeypatch.setenv("FOGAR_GATEWAY_HOME", str(tmp_path))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("PUBLIC_URLS", raising=False)
    env = tmp_path / "config.env"
    env.write_text(f"ADMIN_TOKEN={ADMIN}\n")
    s = SimpleNamespace(env=env, queue=[], asked=[], status=RUNNING, serve={},
                        enable=[("ok", "")], enabled=[], sudo=[])
    monkeypatch.setattr(output, "_stdin_isatty", lambda: True)
    monkeypatch.setattr(access_mod, "tailscale_status", lambda exe="tailscale": s.status)
    monkeypatch.setattr(access_mod, "serve_status", lambda exe="tailscale": s.serve)

    def enable(port, gw_port, exe="tailscale", timeout=15):
        s.enabled.append((port, gw_port))
        return s.enable.pop(0)

    monkeypatch.setattr(cli_access, "enable_serve", enable)
    monkeypatch.setattr(cli_access.subprocess, "run", lambda cmd, **kw: s.sudo.append(cmd))
    monkeypatch.setattr(cli_access, "gateway_instance_id", lambda url: "abc")
    monkeypatch.setattr(access_mod, "collect", lambda *a, **k: [])

    def question(kind):
        class _Q:
            def __init__(self, message, *args, **kwargs):
                self.kwargs = kwargs
                s.asked.append((kind, message, kwargs))

            def ask(self):
                answer = s.queue.pop(0) if s.queue else None
                validate = self.kwargs.get("validate")
                if kind == "text" and validate and answer is not None:
                    assert validate(answer) is True, validate(answer)
                return answer
        return _Q

    for kind in ("select", "text", "confirm"):
        monkeypatch.setattr(questionary, kind, question(kind))
    return s


def setup():
    return CliRunner().invoke(app.cli, ["access", "setup"])


def test_unreadable_serve_status_is_left_alone(st):
    st.serve = None
    st.queue = ["tailscale"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert "No puedo leer `tailscale serve status`: no lo toco." in r.output
    assert st.enabled == []


def test_setup_offers_one_choice_with_four_ways(st):
    st.queue = ["lan"]
    r = setup()
    assert r.exit_code == 0, r.output
    kind, message, kw = st.asked[0]
    # Una sola elección (#328): con casillas no quedaba claro qué se había elegido.
    assert kind == "select" and message == "¿Desde dónde vas a entrar a Fogar?"
    assert [c.value for c in kw["choices"]] == ["lan", "tailscale", "domain", "tailscale+domain"]
    assert kw["choices"][0].title == "Desde casa (red local)" and kw["default"] == "lan"
    assert st.enabled == [] and "PUBLIC_URLS" not in dotenv_values(st.env)
    assert "Acceso: desde casa." in r.output


def test_setup_summary_says_what_was_left_ready(st):
    st.serve = OURS
    st.queue = ["tailscale+domain", "https://fogar.example.com"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert "Acceso: desde casa, con Tailscale y con tu dominio." in r.output


def test_setup_summary_leaves_out_a_skipped_tailscale(st):
    st.queue = ["tailscale", False]  # no quiere activar `tailscale serve`
    r = setup()
    assert r.exit_code == 0, r.output
    assert "Tailscale queda sin activar" in r.output and "Acceso: desde casa." in r.output


def test_setup_saves_public_url(st):
    st.queue = ["domain", "https://fogar.example.com"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert dotenv_values(st.env)["PUBLIC_URLS"] == "https://fogar.example.com/"
    assert "Haz que apunte a http://localhost:8004" in r.output


def test_setup_rejects_bad_domain_in_prompt(st):
    st.queue = ["domain", "https://fogar.example.com"]
    setup()
    validate = next(kw for k, _, kw in st.asked if k == "text")["validate"]
    assert validate("http://fogar.example.com") is not True
    assert validate("") is not True


def test_setup_keeps_existing_public_urls(st):
    st.env.write_text(f"ADMIN_TOKEN={ADMIN}\nPUBLIC_URLS=https://a.example.com/\n")
    st.queue = ["domain", "https://b.example.com, https://a.example.com"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert dotenv_values(st.env)["PUBLIC_URLS"] == "https://a.example.com/,https://b.example.com/"


def test_setup_serve_already_on_does_nothing(st):
    st.serve = OURS
    st.queue = ["tailscale"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert st.enabled == []
    assert f"Ya está: https://{DNS}/" in r.output
    assert not [a for a in st.asked if a[0] == "confirm"]  # ni pregunta


def test_setup_serve_enables_free_port(st):
    st.serve = {"TCP": {"443": {}}}  # 443 sirve otra cosa: 8443
    st.queue = ["tailscale", True]
    r = setup()
    assert r.exit_code == 0, r.output
    assert st.enabled == [(8443, 8004)]
    assert f"https://{DNS}:8443/" in r.output


def test_setup_serve_declined_does_nothing(st):
    st.queue = ["tailscale", False]
    r = setup()
    assert r.exit_code == 0, r.output
    assert st.enabled == []


def test_setup_serve_ports_taken(st):
    st.serve = {"TCP": {"443": {}, "8443": {}}}
    st.queue = ["tailscale", True]
    r = setup()
    assert r.exit_code == 0, r.output
    assert "443 y 8443 ya sirven otra cosa: no lo toco" in r.output
    assert st.enabled == []


def test_setup_permission_offers_sudo_and_retries(st, monkeypatch):
    monkeypatch.setattr(cli_access.getpass, "getuser", lambda: "sito")
    st.enable = [("permiso", ""), ("ok", "")]
    st.queue = ["tailscale", True, "sudo"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert "sudo tailscale set --operator=$USER" in r.output
    assert st.sudo == [["sudo", "tailscale", "set", "--operator=sito"]]
    assert st.enabled == [(443, 8004), (443, 8004)]  # un reintento


def test_setup_permission_retries_only_once(st):
    st.enable = [("permiso", ""), ("permiso", "")]
    st.queue = ["tailscale", True, "sudo"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert len(st.sudo) == 1 and len(st.enabled) == 2


def test_setup_permission_skip_runs_nothing(st):
    st.enable = [("permiso", "")]
    st.queue = ["tailscale", True, "skip"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert st.sudo == [] and len(st.enabled) == 1


def test_setup_https_off_shows_link_and_retries(st):
    link = "https://login.tailscale.com/f/serve?node=abc"
    st.enable = [("https_off", link), ("ok", "")]
    st.queue = ["tailscale", True, "retry"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert link in r.output and len(st.enabled) == 2


def test_setup_tailscale_missing_shows_install_commands(st):
    st.status = None
    st.queue = ["tailscale", "skip"]
    r = setup()
    assert r.exit_code == 0, r.output
    assert "curl -fsSL https://tailscale.com/install.sh | sh" in r.output
    assert "sudo tailscale up" in r.output
    assert st.enabled == []


def test_setup_tailscale_redetects_after_install(st, monkeypatch):
    st.serve = OURS
    st.queue = ["tailscale", "retry"]
    seen = iter([None, None, RUNNING])  # la 1.ª, al mirar qué hay ya
    monkeypatch.setattr(access_mod, "tailscale_status", lambda exe="tailscale": next(seen))
    r = setup()
    assert r.exit_code == 0, r.output
    assert f"Ya está: https://{DNS}/" in r.output


def test_setup_needs_terminal(st, monkeypatch):
    monkeypatch.setattr(output, "_stdin_isatty", lambda: False)
    r = setup()
    assert r.exit_code == 1
    assert "terminal" in r.output and st.asked == []


def test_setup_ctrl_c_aborts(st):
    st.queue = [None]
    r = setup()
    assert r.exit_code == 1 and "Aborted!" in r.output


def test_setup_starts_from_what_is_already_there(st):
    st.serve = OURS  # `tailscale serve` ya lleva al gateway
    st.env.write_text(f"ADMIN_TOKEN={ADMIN}\nPUBLIC_URLS=https://a.example.com/\n")
    st.queue = ["lan"]  # elegir «Desde casa» no quita nada: el resumen lo dice
    r = setup()
    assert r.exit_code == 0, r.output
    assert st.asked[0][2]["default"] == "tailscale+domain"
    assert "Acceso: desde casa, con Tailscale y con tu dominio." in r.output
