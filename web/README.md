# Web de fogar-gateway

El panel de Fogar: conectar con el token de administrador, el estado en vivo (con el acceso del gateway a OpenClaw), las conversaciones de la casa (leer las de cada aparato; crear las del panel con su agente y chatear en ellas por texto), las sesiones y los clientes (emparejar, crear, editar, rotar la clave, activar y borrar). Lo sirve el propio gateway en `/` (`src/fogar_gateway/api/web_routes.py`). Vue 3 + TypeScript + Vite + Pinia, sin librería de componentes, con la identidad de `fogar-brand`.

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

## Chat (M19)

El chat entra en `/ws/stream` con el token de administrador (`admin_token`) como el cliente «Panel web» (`panel-web`), siempre en una conversación concreta (`conversation` con su id; las crea `POST /admin/conversations`), nunca en la del día: así dos pestañas no se pisan. Solo texto (`input_mode: "text"`, `output_mode: ["text", "status"]`; `status` trae `turn_progress`, que la respuesta en vivo enseña como «Pensando…» o «Usando <herramienta>…» hasta el primer token, #254); la voz en la web queda fuera de M19. La lógica está en `src/lib/chat.ts` (mensajes, motivos de `turn_end`, qué hacer en cada cierre), `src/lib/closeCodes.ts` (los códigos de cierre y su clase de reconexión, copia de `src/fogar_gateway/models/close_codes.py` que `tests/unit/test_close_codes.py` compara) y `src/lib/chatSession.ts` (el socket), sin componentes, con sus pruebas. Cada cierre se decide por el código, nunca por el texto del `reason` (`docs/client-protocol.md` §13, «Códigos de cierre»): 4102 (conversación abierta en otro sitio) no reconecta solo y ofrece «Seguir aquí»; 1000 (inactividad) se reabre al escribir; el resto de 4000–4899 para con su aviso y «Reintentar»; 49xx y los cierres de transporte (1006, 1011, 1012, 1013…) reintentan con espera creciente. Las conversaciones de otros aparatos solo se leen.

Spec, plan y decisiones: en Obsidian, spec «Dashboard de Fogar (M18)» del 2026-11-12.
