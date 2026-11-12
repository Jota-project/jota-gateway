# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users
El dueño de una instalación casera de Fogar (hoy, una sola persona: Sito). Entra desde el Mac o el móvil, en casa o por Tailscale, para comprobar que todo responde, gestionar y emparejar los clientes (pantalla, voz, Home Assistant, app), revisar sesiones y hablar con Fogar.

## Product Purpose
La web de fogar-gateway es su panel de administración y su cliente de chat. La sirve el propio gateway en `/`, comparte origen con la API y no necesita nada más instalado. Tiene éxito cuando el dueño ve de un vistazo si Fogar funciona y cambia lo que la API permite sin ir a la terminal (`fogar-gw`).

## Positioning
Es la cara de un asistente propio en casa: enseña el estado real del gateway, en vivo y sin adornos, con el lenguaje visual de la pantalla de Fogar (Fogar TV).

## Operating Context
- Pantallas: Conectar (token de administrador), Estado, Sesiones, Clientes (con emparejamiento por código de 6 cifras) y Chat (texto y voz).
- Datos en vivo por `/admin/state/stream` (fetch, sin EventSource) y endpoints de `/admin/*`; especificación en Obsidian, «JGW Spec 2026-11-02 - Web en Vue».
- El micrófono solo funciona con HTTPS o en `localhost`; por HTTP la web funciona sin micro.

## Capabilities and Constraints
- Solo enseña lo que el gateway expone hoy; nada se pinta si la API no lo da.
- Credenciales en el navegador: el token de administrador y, desde 17e, la clave del cliente web del chat. CSP estricta, ningún recurso de fuera, nunca `v-html`; la clave de un cliente solo se ve al crearlo o rotarlo.
- La web es un cliente de administrador: abre códigos de emparejamiento, nunca los teclea.
- Decisiones abiertas: estilo de los mensajes del chat (Transcripción o Escenario), entrar sin pegar el token (enlace con token o código de un solo uso), onboarding en la web (fuera de M17).

## Brand Commitments
- Nombre: Fogar. Textos solo en español, tono cercano y directo («Hola. Conectemos con tu gateway.»).
- Sistema de diseño Fogar Dusk, variante oscura de JScreen; el modo oscuro es el único en la v1.

## Evidence on Hand
Ninguna: no hay testimonios, cifras ni capturas de usuarios. No inventarlos.

## Product Principles
- Decir la verdad del estado: si algo falla o no hay conexión, se ve.
- Lo que se usa cien veces al día va rápido; con teclado nada espera a una animación.
- Menos pantalla y menos código: nada que el gateway no sepa todavía.
- Las credenciales no se enseñan ni se registran más de lo imprescindible.

## Accessibility & Inclusion
WCAG AA en oscuro. Siempre se respeta «reducir movimiento». Todo funciona con teclado y lector de pantalla.
