import asyncio
import threading

from cachetools import TTLCache

from src.core.cache import make_cache


def test_make_cache_returns_ttlcache_instance():
    cache, _ = make_cache(maxsize=10, ttl=5)
    assert isinstance(cache, TTLCache)


def test_make_cache_lock_is_a_real_thread_lock_not_asyncio_lock():
    _, lock = make_cache(maxsize=10, ttl=5)
    assert not isinstance(lock, asyncio.Lock)
    assert isinstance(lock, type(threading.Lock()))


def test_make_cache_lock_is_acquirable_from_a_different_thread():
    _, lock = make_cache(maxsize=10, ttl=5)
    acquired_in_other_thread = {}

    def worker():
        acquired_in_other_thread["ok"] = lock.acquire(timeout=1)
        if acquired_in_other_thread["ok"]:
            lock.release()

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=2)

    assert acquired_in_other_thread.get("ok") is True
