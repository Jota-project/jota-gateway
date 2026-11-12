# Web de fogar-gateway

Chat con Fogar, estado en vivo, sesiones y clientes. La sirve el propio gateway en `/` (milestone M17). Vue 3 + TypeScript + Vite, con el sistema visual de JScreen (Fogar Dusk oscuro).

Estado: **esqueleto**. Hecho: Conectar con el token de administrador, barra lateral con el estado vivo de los servicios y un primer corte de Estado. Chat, Sesiones y Clientes son huecos.

## Desarrollo

```bash
cd web
pnpm install
pnpm dev                                       # http://localhost:5173, API en 127.0.0.1:8004
FOGAR_GATEWAY_URL=http://otro-host:8004 pnpm dev  # contra otro gateway
pnpm test
pnpm build                                     # tipos + build en web/dist
```

Vite reenvía `/admin`, `/ready`, `/healthz`, `/v1` y `/ws` al gateway, así que la web y la API comparten origen como en producción. Hace falta un gateway con `ADMIN_TOKEN`.

Spec, decisiones y diseño: en Obsidian, `JGW Spec 2026-11-02 - Web en Vue` y `JGW M17 - Web del gateway`.
