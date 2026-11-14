#!/bin/sh
# Instalador de fogar-gateway (Linux con systemd de usuario):
#   curl -fsSL https://github.com/fogarhome/fogar-gateway/releases/latest/download/install.sh | sh
# Instala `fogar-gw` con `uv tool` y, la primera vez, lanza `fogar-gw onboard`; si el servicio ya
# está instalado, actualiza y lo reinicia sin preguntar. `sh -s -- --no-onboard` solo instala.
# Versión: FOGAR_VERSION=X.Y.Z; si no, la sellada abajo en cada release; si no, la última.
# Todo va dentro de `main`, llamada en la última línea: una descarga cortada no ejecuta nada.
# Idioma de los mensajes: LC_ALL, LC_MESSAGES o LANG (el primero que esté definido); «es*» en
# español y cualquier otro valor (o ninguno) en inglés. / Messages follow LC_ALL, LC_MESSAGES or
# LANG: Spanish for «es*», English otherwise.
# FOGAR_INSTALL_LIB=1 (solo tests): define las funciones y no ejecuta `main`.

# La sella el prepareCmd de .releaserc.json en el asset de la release; en el repo, vacía.
FOGAR_RELEASE=

REPO=https://github.com/fogarhome/fogar-gateway

say() { printf '%s\n' "$*"; }

# Deja en `lang` el idioma de los mensajes: es o en.
pick_lang() {
  locale_name=${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}
  case $locale_name in
    es*) lang=es ;;
    *) lang=en ;;
  esac
}

# t TEXTO_ES TEXTO_EN: el texto en el idioma elegido.
t() {
  if [ "${lang:-en}" = es ]; then printf '%s' "$1"; else printf '%s' "$2"; fi
}

# is_yes RESPUESTA: éxito solo con s, si, sí, y o yes, sin distinguir mayúsculas (sí va en UTF-8: se
# compara por bytes, y «SÍ» también). Vacío u otra cosa, no.
is_yes() {
  case $1 in
    [sS] | [sS][iI] | [sS]'í' | [sS]'Í' | [yY] | [yY][eE][sS]) return 0 ;;
    *) return 1 ;;
  esac
}

fail() {
  printf '%s\n' "$*" >&2
  exit 1
}

# El probe va en un subshell: con dash, una redirección fallida en `:` saldría del script.
has_tty() { (: </dev/tty) 2>/dev/null; }

# offer MOTIVO COMANDO...: muestra el comando y lo ejecuta solo con un sí (is_yes) leído del terminal.
offer() {
  why=$1
  shift
  answer=
  if has_tty; then
    prompt=$(t "$why: hace falta «$*»; ¿lo ejecuto yo? [s/N] " "$why: «$*» is needed; shall I run it? [y/N] ")
    printf '%s' "$prompt" >/dev/tty
    read -r answer </dev/tty || answer=
  fi
  if ! is_yes "$answer"; then
    fail "$(t "$why. Ejecuta «$*» y vuelve a lanzar el instalador." "$why. Run «$*» and launch the installer again.")"
  fi
  "$@" || fail "$(t "«$*» ha fallado." "«$*» failed.")"
}

# Deja la versión X.Y.Z en `version`: FOGAR_VERSION, la sellada o la última release.
resolve_version() {
  version=${FOGAR_VERSION:-$FOGAR_RELEASE}
  if [ -z "$version" ]; then
    url=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "$REPO/releases/latest") ||
      fail "$(t "No puedo consultar la última versión en $REPO/releases." "Cannot look up the latest version at $REPO/releases.")"
    version=${url##*/tag/v}
  fi
  version=${version#v}
  # Exactamente X.Y.Z, solo dígitos: va a una URL y a la especificación de uv.
  case $version in
    '' | *[!0-9.]* | .* | *. | *..* | *.*.*.*) ;;
    *.*.*) return 0 ;;
  esac
  fail "$(t "Versión no válida: «$version». Usa FOGAR_VERSION=X.Y.Z." "Invalid version: «$version». Use FOGAR_VERSION=X.Y.Z.")"
}

main() {
  set -eu
  pick_lang
  no_onboard=
  for arg in "$@"; do
    case $arg in
      --no-onboard) no_onboard=1 ;;
      *) fail "$(t "Opción desconocida: $arg. Uso: install.sh [--no-onboard]" "Unknown option: $arg. Usage: install.sh [--no-onboard]")" ;;
    esac
  done

  [ "$(uname -s)" = Linux ] ||
    fail "$(t "Este instalador es solo para Linux (con systemd). En otros sistemas, usa Docker: $REPO#docker-alternativa" "This installer is for Linux (with systemd) only. On other systems, use Docker: $REPO#docker-alternativa")"
  case $(uname -m) in
    x86_64 | aarch64) ;;
    *) fail "$(t "Arquitectura no soportada: $(uname -m). Solo x86_64 o aarch64." "Unsupported architecture: $(uname -m). Only x86_64 or aarch64.")" ;;
  esac
  [ "$(id -u)" != 0 ] ||
    fail "$(t "No lo ejecutes como root: fogar-gateway es un servicio de systemd de tu usuario. Lánzalo con tu usuario; el instalador pide sudo solo para lo que lo necesita." "Do not run it as root: fogar-gateway is a systemd service of your own user. Launch it as your user; the installer asks for sudo only for what needs it.")"

  if ! systemctl --user show-environment >/dev/null 2>&1; then
    fail "$(t "No hay systemd de usuario (\`systemctl --user\`), y fogar-gateway se instala como servicio de usuario. Si has entrado con su o sudo -iu, entra por SSH o define XDG_RUNTIME_DIR=/run/user/$(id -u)." "There is no user systemd (\`systemctl --user\`), and fogar-gateway installs as a user service. If you logged in with su or sudo -iu, log in over SSH or set XDG_RUNTIME_DIR=/run/user/$(id -u).")"
  fi
  if ! command -v git >/dev/null 2>&1; then
    command -v apt-get >/dev/null 2>&1 ||
      fail "$(t "Falta git: instálalo con el gestor de paquetes de tu distribución y vuelve a lanzar el instalador." "git is missing: install it with your distribution's package manager and launch the installer again.")"
    offer "$(t "Falta git" "git is missing")" sudo apt-get install -y git
  fi
  user=$(id -un)  # no $USER: puede venir heredado (su, sudo) y nombrar a otro usuario
  if [ "$(loginctl show-user "$user" -p Linger --value 2>/dev/null || true)" != yes ]; then
    offer "$(t "Sin linger, el servicio se para al cerrar tu sesión" "Without linger, the service stops when you log out")" sudo loginctl enable-linger "$user"
  fi

  # Una shell de ssh sin login no suele llevar ~/.local/bin, que es donde están uv y fogar-gw.
  user_path=$PATH
  PATH="$HOME/.local/bin:$PATH"
  export PATH
  if ! command -v uv >/dev/null 2>&1; then
    say "$(t "Instalando uv…" "Installing uv…")"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    command -v uv >/dev/null 2>&1 || fail "$(t "No se ha podido instalar uv." "Could not install uv.")"
  fi

  # FOGAR_SOURCE (solo tests): una ruta local, con sus versiones de uv.lock, o una especificación para uv.
  if [ -d "${FOGAR_SOURCE:-}" ]; then
    spec=$FOGAR_SOURCE
    constraints=$(mktemp)
    trap 'rm -f "$constraints"' EXIT
    uv export --project "$FOGAR_SOURCE" --frozen --no-dev --no-emit-project --no-hashes --quiet -o "$constraints"
    say "$(t "Instalando fogar-gateway desde $FOGAR_SOURCE…" "Installing fogar-gateway from $FOGAR_SOURCE…")"
  else
    resolve_version
    # El wheel de la release lleva la web compilada (M18): sin él no habría panel en /.
    spec=${FOGAR_SOURCE:-"fogar-gateway @ $REPO/releases/download/v$version/fogar_gateway-$version-py3-none-any.whl"}
    constraints=$REPO/releases/download/v$version/constraints.txt
    say "$(t "Instalando fogar-gateway v$version…" "Installing fogar-gateway v$version…")"
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
    *) path_note=$(t "Si esta terminal no encuentra \`fogar-gw\`, abre otra nueva o usa $tool_bin/fogar-gw." "If this terminal cannot find \`fogar-gw\`, open a new one or use $tool_bin/fogar-gw.") ;;
  esac

  unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/fogar-gateway.service"
  if [ -e "$unit" ]; then
    fogar-gw --yes restart
    fogar-gw doctor || say "$(t "El diagnóstico marca algún fallo (arriba); la actualización está hecha." "The diagnosis reports a problem (above); the update is done.")"
    [ -z "$path_note" ] || say "$path_note"
    return
  fi
  # shellcheck disable=SC2016 # las comillas invertidas son texto
  todo=$(t 'Instalado. Configúralo con `fogar-gw onboard` o, si vienes de Docker, `fogar-gw migrate --from-docker DIR` y `fogar-gw install`.' 'Installed. Set it up with `fogar-gw onboard` or, if you come from Docker, `fogar-gw migrate --from-docker DIR` and `fogar-gw install`.')
  interactive=
  if [ -z "$no_onboard" ] && [ -t 1 ] && has_tty; then
    interactive=1
  fi
  if ! fogar-gw onboard --help >/dev/null 2>&1; then  # las versiones anteriores al asistente
    [ -z "$interactive" ] ||
      fail "$(t "Esta versión de fogar-gateway no tiene \`fogar-gw onboard\`: instala una más nueva o vuelve a lanzar el instalador con \`sh -s -- --no-onboard\` y configúralo a mano ($REPO#sin-el-instalador)." "This version of fogar-gateway has no \`fogar-gw onboard\`: install a newer one or launch the installer again with \`sh -s -- --no-onboard\` and set it up by hand ($REPO#sin-el-instalador).")"
    todo=$(t "Instalado. Esta versión no tiene \`fogar-gw onboard\`: configúralo a mano ($REPO#sin-el-instalador)." "Installed. This version has no \`fogar-gw onboard\`: set it up by hand ($REPO#sin-el-instalador).")
  fi
  if [ -n "$interactive" ]; then
    fogar-gw onboard </dev/tty
  else
    say "$todo"
  fi
  [ -z "$path_note" ] || say "$path_note"
}

[ -n "${FOGAR_INSTALL_LIB:-}" ] || main "$@"
