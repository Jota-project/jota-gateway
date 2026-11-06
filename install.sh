#!/bin/sh
# Instalador de jota-gateway (Linux con systemd de usuario):
#   curl -fsSL https://github.com/Jota-project/jota-gateway/releases/latest/download/install.sh | sh
# Instala `jota-gw` con `uv tool` y, la primera vez, lanza `jota-gw onboard`; si el servicio ya
# está instalado, actualiza y lo reinicia sin preguntar. `sh -s -- --no-onboard` solo instala.
# Versión: JOTA_VERSION=X.Y.Z; si no, la sellada abajo en cada release; si no, la última.
# Todo va dentro de `main`, llamada en la última línea: una descarga cortada no ejecuta nada.

# La sella el prepareCmd de .releaserc.json en el asset de la release; en el repo, vacía.
JOTA_RELEASE=

REPO=https://github.com/Jota-project/jota-gateway

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

# Deja la versión X.Y.Z en `version`: JOTA_VERSION, la sellada o la última release.
resolve_version() {
  version=${JOTA_VERSION:-$JOTA_RELEASE}
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
  fail "Versión no válida: «$version». Usa JOTA_VERSION=X.Y.Z."
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
    fail "No lo ejecutes como root: jota-gateway es un servicio de systemd de tu usuario. Lánzalo con tu usuario; el instalador pide sudo solo para lo que lo necesita."

  if ! systemctl --user show-environment >/dev/null 2>&1; then
    fail "No hay systemd de usuario (\`systemctl --user\`), y jota-gateway se instala como servicio de usuario. Si has entrado con su o sudo -iu, entra por SSH o define XDG_RUNTIME_DIR=/run/user/$(id -u)."
  fi
  if ! command -v git >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 ||
      fail "Falta git: instálalo con el gestor de paquetes de tu distribución y vuelve a lanzar el instalador."
    offer "Falta git" sudo apt-get install -y git
  fi
  user=${USER:-$(id -un)}
  if [ "$(loginctl show-user "$user" -p Linger --value 2>/dev/null || true)" != yes ]; then
    offer "Sin linger, el servicio se para al cerrar tu sesión" sudo loginctl enable-linger "$user"
  fi

  # Una shell de ssh sin login no suele llevar ~/.local/bin, que es donde están uv y jota-gw.
  user_path=$PATH
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  if ! command -v uv >/dev/null 2>&1; then
    say "Instalando uv…"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    command -v uv >/dev/null 2>&1 || fail "No se ha podido instalar uv."
  fi

  # JOTA_SOURCE (solo tests): una ruta local, con sus versiones de uv.lock, o una especificación para uv.
  if [ -d "${JOTA_SOURCE:-}" ]; then
    spec=$JOTA_SOURCE
    constraints=$(mktemp)
    trap 'rm -f "$constraints"' EXIT
    uv export --project "$JOTA_SOURCE" --frozen --no-dev --no-emit-project --no-hashes --quiet -o "$constraints"
    say "Instalando jota-gateway desde $JOTA_SOURCE…"
  else
    resolve_version
    spec=${JOTA_SOURCE:-"jota-gateway @ git+$REPO@v$version"}
    constraints=$REPO/releases/download/v$version/constraints.txt
    say "Instalando jota-gateway v$version…"
  fi
  uv tool install --reinstall "$spec" -c "$constraints"
  # Con el PATH del usuario: si ya lleva el directorio, update-shell no cambia nada. Si no
  # reconoce la shell, falla con su motivo; no impide seguir (aquí el PATH se fija abajo).
  uv_bin=$(command -v uv)
  PATH=$user_path "$uv_bin" tool update-shell || true
  PATH="$(uv tool dir --bin):$PATH"
  export PATH

  unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/jota-gateway.service"
  if [ -e "$unit" ]; then
    jota-gw --yes restart
    jota-gw doctor || say "El diagnóstico marca algún fallo (arriba); la actualización está hecha."
    return
  fi
  # shellcheck disable=SC2016 # las comillas invertidas son texto
  todo='Instalado. Configúralo con `jota-gw onboard`.'
  interactive=
  if [ -z "$no_onboard" ] && [ -t 1 ] && has_tty; then
    interactive=1
  fi
  if ! jota-gw onboard --help >/dev/null 2>&1; then  # las versiones anteriores al asistente
    [ -z "$interactive" ] ||
      fail "Esta versión de jota-gateway no tiene \`jota-gw onboard\`: instala una más nueva o vuelve a lanzar el instalador con \`sh -s -- --no-onboard\` y configúralo a mano ($REPO#sin-el-instalador)."
    todo="Instalado. Esta versión no tiene \`jota-gw onboard\`: configúralo a mano ($REPO#sin-el-instalador)."
  fi
  if [ -n "$interactive" ]; then
    jota-gw onboard </dev/tty
  else
    say "$todo"
  fi
}

main "$@"
