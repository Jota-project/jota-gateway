import asyncio
from cachetools import TTLCache


def make_cache(maxsize: int, ttl: int) -> tuple[TTLCache, asyncio.Lock]:
    """
    Crea un par (cache, lock) listo para usar en métodos async.

    Args:
        maxsize: Número máximo de entradas. Las más antiguas se expulsan (LRU) al superarse.
        ttl:     Segundos hasta que una entrada expira automáticamente.

    Returns:
        (TTLCache, asyncio.Lock) — el lock debe envolver SOLO el acceso al dict,
        nunca el IO, para no bloquear el event loop.

    Example::

        _cache, _lock = make_cache(maxsize=500, ttl=60)

        async with _lock:
            if key in _cache:
                return _cache[key]
        result = await fetch(key)          # IO fuera del lock
        async with _lock:
            _cache[key] = result
        return result
    """
    return TTLCache(maxsize=maxsize, ttl=ttl), asyncio.Lock()
