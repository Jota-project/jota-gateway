#!/bin/sh
# Compila la web (web/) y la deja dentro del paquete (src/fogar_gateway/web/), donde la sirve
# api/web_routes.py y de donde la recoge el wheel. La usan la release (.releaserc.json), la CI y
# el Dockerfile; en el destino no hace falta Node.
set -eu
cd "$(dirname "$0")/.."
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
rm -rf src/fogar_gateway/web
cp -R web/dist src/fogar_gateway/web
