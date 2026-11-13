# Web de fogar-gateway

El panel de Fogar: conectar con el token de administrador, el estado en vivo, las sesiones y los clientes (emparejar, crear, editar, rotar la clave, activar y borrar). Lo sirve el propio gateway en `/` (`src/fogar_gateway/api/web_routes.py`). Vue 3 + TypeScript + Vite + Pinia, sin librería de componentes, con la identidad de `fogar-brand`.

## Desarrollo

```bash
cd web
pnpm install
pnpm dev                                          # http://localhost:5173, API en 127.0.0.1:8004
FOGAR_GATEWAY_URL=http://otro-host:8004 pnpm dev  # contra otro gateway
pnpm test
pnpm typecheck
pnpm build                                        # tipos + build en web/dist
```

Vite reenvía `/admin`, `/ready`, `/healthz`, `/v1` y `/ws` al gateway, así que la web y la API comparten origen como en producción. Hace falta un gateway con `ADMIN_TOKEN`.

Para probarla servida por el gateway: `sh scripts/build-web.sh` (desde la raíz) la compila y la copia a `src/fogar_gateway/web/`, que es lo que lleva el wheel de cada release.

## Marca

Todo sale de `fogar-brand`, copiado y sin editar a mano:

- `src/styles/tokens.css`: el bloque de arriba es `sistema/project/tokens.css`; debajo, lo propio del panel (`vidrio-fino`).
- `src/assets/fonts/` y `public/fonts/`: Unbounded y Figtree (`fuentes/`) con su OFL.
- `src/lib/lineaViva.js`: el motor de `linea-viva/index.html`, envuelto para tener varias instancias.
- `public/favicon.svg` (`web/favicon.svg`) y los iconos de estado (`iconos/estados/`).

Reglas que no se negocian: nada se carga de fuera (CSP `default-src 'self'`), nunca `v-html` ni `style="…"` estático en las plantillas (la CSP no deja estilos en línea), la clave de un cliente y el código de emparejamiento solo existen en la pantalla que los enseña.

Spec, plan y decisiones: en Obsidian, spec «Dashboard de Fogar (M18)» del 2026-11-12.
