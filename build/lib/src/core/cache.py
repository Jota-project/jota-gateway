import threading

from cachetools import TTLCache


def make_cache(maxsize: int, ttl: int) -> tuple[TTLCache, threading.Lock]:
    """
    Crea un par (cache, lock) listo para usar tanto desde código async
    (event loop) como desde handlers síncronos ejecutados en el threadpool
    de Starlette (p.ej. rutas `def` de admin_routes.py).

    Args:
        maxsize: Número máximo de entradas. Las más antiguas se expulsan (LRU) al superarse.
        ttl:     Segundos hasta que una entrada expira automáticamente.

    Returns:
        (TTLCache, threading.Lock) — el lock es un mutex real de SO, seguro
        de adquirir desde cualquier hilo (a diferencia de asyncio.Lock, que
        se vincula al primer event loop que lo usa y no es seguro entre
        hilos). Debe envolver SOLO el acceso al dict — incluyendo el
        housekeeping interno de expiración que TTLCache dispara en `in`,
        `[]` y `.pop()` — nunca el IO. Las secciones críticas son
        operaciones de dict puras del orden de microsegundos, así que
        bloquear con él dentro de una corrutina no bloquea el event loop
        de forma apreciable.

    Example::

        _cache, _lock = make_cache(maxsize=500, ttl=60)

        with _lock:
            if key in _cache:
                return _cache[key]
        result = await fetch(key)          # IO fuera del lock
        with _lock:
            _cache[key] = result
        return result
    """
    return TTLCache(maxsize=maxsize, ttl=ttl), threading.Lock()
