#!/bin/sh
# Instalador de fogar-gateway (Linux con systemd de usuario):
#   curl -fsSL https://github.com/fogarhome/fogar-gateway/releases/latest/download/install.sh | sh
# Instala `fogar-gw` con `uv tool` y, la primera vez, lanza `fogar-gw onboard`; si el servicio ya
# está instalado, actualiza y lo reinicia sin preguntar. `sh -s -- --no-onboard` solo instala.
# Versión: FOGAR_VERSION=X.Y.Z; si no, la sellada abajo en cada release; si no, la última.
# Todo va dentro de `main`, llamada en la última línea: una descarga cortada no ejecuta nada.

# La sella el prepareCmd de .releaserc.json en el asset de la release; en el repo, vacía.
FOGAR_RELEASE=

REPO=https://github.com/fogarhome/fogar-gateway

say() { printf '%s\n' "$*"; }

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

# El probe va en un subshell: con dash, una redirección fallida en `:` saldría del script.
has_tty() { (: </dev/tty) 2>/dev/null; }

# offer MOTIVO COMANDO...: muestra el comando y lo ejecuta solo con una «s» leída del terminal.
offer() {
  why=$1
  shift
  answer=
  if has_tty; then
    printf '%s: hace falta «%s»; ¿lo ejecuto yo? [s/N] ' "$why" "$*" >/dev/tty
    read -r answer </dev/tty || answer=
  fi
  if [ "$answer" != s ]; then
    fail "$why. Ejecuta «$*» y vuelve a lanzar el instalador."
  fi
  "$@" || fail "«$*» ha fallado."
}

# Deja la versión X.Y.Z en `version`: FOGAR_VERSION, la sellada o la última release.
resolve_version() {
  version=${FOGAR_VERSION:-$FOGAR_RELEASE}
  if [ -z "$version" ]; then
    url=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO/releases/latest") ||
      fail "No puedo consultar la última versión en $REPO/releases."
    version=${url##*/tag/v}
  fi
  version=${version#v}
  # Exactamente X.Y.Z, solo dígitos: va a una URL y a la especificación de uv.
  case $version in
    '' | *[!0-9.]* | .* | *. | *..* | *.*.*.*) ;;
    *.*.*) return 0 ;;
  esac
  fail "Versión no válida: «$version». Usa FOGAR_VERSION=X.Y.Z."
}

main() {
  set -eu
  no_onboard=
  for arg in "$@"; do
    case $arg in
      --no-onboard) no_onboard=1 ;;
      *) fail "Opción desconocida: $arg. Uso: install.sh [--no-onboard]" ;;
    esac
  done

  [ "$(uname -s)" = Linux ] ||
    fail "Este instalador es solo para Linux (con systemd). En otros sistemas, usa Docker: $REPO#docker-alternativa"
  case $(uname -m) in
    x86_64 | aarch64) ;;
    *) fail "Arquitectura no soportada: $(uname -m). Solo x86_64 o aarch64." ;;
  esac
  [ "$(id -u)" != 0 ] ||
    fail "No lo ejecutes como root: fogar-gateway es un servicio de systemd de tu usuario. Lánzalo con tu usuario; el instalador pide sudo solo para lo que lo necesita."

  if ! systemctl --user show-environment >/dev/null 2>&1; then
    fail "No hay systemd de usuario (\`systemctl --user\`), y fogar-gateway se instala como servicio de usuario. Si has entrado con su o sudo -iu, entra por SSH o define XDG_RUNTIME_DIR=/run/user/$(id -u)."
  fi
  if ! command -v git >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 ||
      fail "Falta git: instálalo con el gestor de paquetes de tu distribución y vuelve a lanzar el instalador."
    offer "Falta git" sudo apt-get install -y git
  fi
  user=$(id -un)  # no $USER: puede venir heredado (su, sudo) y nombrar a otro usuario
  if [ "$(loginctl show-user "$user" -p Linger --value 2>/dev/null || true)" != yes ]; then
    offer "Sin linger, el servicio se para al cerrar tu sesión" sudo loginctl enable-linger "$user"
  fi

  # Una shell de ssh sin login no suele llevar ~/.local/bin, que es donde están uv y fogar-gw.
  user_path=$PATH
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  if ! command -v uv >/dev/null 2>&1; then
    say "Instalando uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    command -v uv >/dev/null 2>&1 || fail "No se ha podido instalar uv."
  fi

  # FOGAR_SOURCE (solo tests): una ruta local, con sus versiones de uv.lock, o una especificación para uv.
  if [ -d "${FOGAR_SOURCE:-}" ]; then
    spec=$FOGAR_SOURCE
    constraints=$(mktemp)
    trap 'rm -f "$constraints"' EXIT
    uv export --project "$FOGAR_SOURCE" --frozen --no-dev --no-emit-project --no-hashes --quiet -o "$constraints"
    say "Instalando fogar-gateway desde $FOGAR_SOURCE…"
  else
    resolve_version
    spec=${FOGAR_SOURCE:-"fogar-gateway @ git+$REPO@v$version"}
    constraints=$REPO/releases/download/v$version/constraints.txt
    say "Instalando fogar-gateway v$version…"
  fi
  uv tool install --reinstall "$spec" -c "$constraints"
  # Con el PATH del usuario: si ya lleva el directorio, update-shell no cambia nada. Si no
  # reconoce la shell, falla con su motivo; no impide seguir (aquí el PATH se fija abajo).
  uv_bin=$(command -v uv)
  PATH=$user_path "$uv_bin" tool update-shell || true
  tool_bin=$(uv tool dir --bin)
  PATH="$tool_bin:$PATH"
  export PATH
  # La terminal desde la que se lanzó no ve el PATH nuevo de update-shell hasta abrir otra.
  path_note=
  case ":$user_path:" in
    *":$tool_bin:"*) ;;
    *) path_note="Si esta terminal no encuentra \`fogar-gw\`, abre otra nueva o usa $tool_bin/fogar-gw." ;;
  esac

  unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/fogar-gateway.service"
  if [ -e "$unit" ]; then
    fogar-gw --yes restart
    fogar-gw doctor || say "El diagnóstico marca algún fallo (arriba); la actualización está hecha."
    [ -z "$path_note" ] || say "$path_note"
    return
  fi
  # shellcheck disable=SC2016 # las comillas invertidas son texto
  todo='Instalado. Configúralo con `fogar-gw onboard` o, si vienes de Docker, `fogar-gw migrate --from-docker DIR` y `fogar-gw install`.'
  interactive=
  if [ -z "$no_onboard" ] && [ -t 1 ] && has_tty; then
    interactive=1
  fi
  if ! fogar-gw onboard --help >/dev/null 2>&1; then  # las versiones anteriores al asistente
    [ -z "$interactive" ] ||
      fail "Esta versión de fogar-gateway no tiene \`fogar-gw onboard\`: instala una más nueva o vuelve a lanzar el instalador con \`sh -s -- --no-onboard\` y configúralo a mano ($REPO#sin-el-instalador)."
    todo="Instalado. Esta versión no tiene \`fogar-gw onboard\`: configúralo a mano ($REPO#sin-el-instalador)."
  fi
  if [ -n "$interactive" ]; then
    fogar-gw onboard </dev/tty
  else
    say "$todo"
  fi
  [ -z "$path_note" ] || say "$path_note"
}

main "$@"
