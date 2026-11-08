"""PairingStore (#317): códigos de emparejamiento en memoria."""

import threading
import time

import pytest

from jota_gateway.db.models import ClientRecord
from jota_gateway.services import pairing
from jota_gateway.services.pairing import InvalidCode, PairingFull, PairingStore


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store(clock: Clock) -> PairingStore:
    return PairingStore(clock=clock)


def _create(calls: list[dict] | None = None):
    def create(template: dict) -> ClientRecord:
        if calls is not None:
            calls.append(template)
        return ClientRecord(id="c1", name=template.get("name", "tele"), client_key="k" * 43)

    return create


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 10**6:06d}"


def test_code_is_six_digits_and_unique_among_open(store, monkeypatch):
    values = iter([42, 42, 7])
    monkeypatch.setattr(pairing.secrets, "randbelow", lambda n: next(values))
    a, b = store.open({}), store.open({})
    assert a.code == "000042"
    assert b.code == "000007"  # el repetido se descarta


def test_code_expires_after_ttl(store, clock):
    item = store.open({})
    clock.now += pairing.CODE_TTL_S
    assert store.get(item.id).state == "expired"
    with pytest.raises(InvalidCode):
        store.redeem(item.code, _create())


def test_max_open_raises(store):
    for _ in range(pairing.MAX_OPEN):
        store.open({})
    with pytest.raises(PairingFull):
        store.open({})


def test_redeem_marks_used_and_returns_client(store):
    calls: list[dict] = []
    item = store.open({"name": "tele", "client_type": "screen"})
    record = store.redeem(item.code[:3] + " " + item.code[3:], _create(calls))
    assert record.id == "c1"
    assert calls == [{"name": "tele", "client_type": "screen"}]
    got = store.get(item.id)
    assert (got.state, got.client_id, got.client_name) == ("used", "c1", "tele")
    with pytest.raises(InvalidCode):
        store.redeem(item.code, _create())


def test_redeem_is_single_use_under_concurrency(store):
    item = store.open({})
    calls: list[dict] = []
    gate = threading.Barrier(2)
    results: list[str] = []

    def slow_create(template: dict) -> ClientRecord:
        calls.append(template)
        time.sleep(0.05)  # si no hubiera candado, el otro hilo entraría aquí
        return ClientRecord(id="c1", name="tele", client_key="k" * 43)

    def worker() -> None:
        gate.wait()
        try:
            store.redeem(item.code, slow_create)
            results.append("ok")
        except InvalidCode:
            results.append("invalid")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["invalid", "ok"]
    assert len(calls) == 1


def test_failed_attempts_burn_all_codes(store):
    a, b = store.open({}), store.open({})
    for _ in range(pairing.MAX_FAILED - 1):
        with pytest.raises(InvalidCode) as exc:
            store.redeem("abc", _create())
        assert not exc.value.burned
    with pytest.raises(InvalidCode) as exc:
        store.redeem("abc", _create())
    assert exc.value.burned
    assert store.get(a.id).state == store.get(b.id).state == "cancelled"
    assert store.list_open() == []


def test_failures_do_not_count_without_open_codes(store):
    for _ in range(pairing.MAX_FAILED + 1):
        with pytest.raises(InvalidCode) as exc:
            store.redeem("123456", _create())
        assert not exc.value.burned
    item = store.open({})
    with pytest.raises(InvalidCode) as exc:
        store.redeem(_wrong(item.code), _create())
    assert not exc.value.burned
    assert store.get(item.id).state == "open"


def test_success_resets_failures(store):
    item, other = store.open({}), store.open({})
    for _ in range(pairing.MAX_FAILED - 1):
        with pytest.raises(InvalidCode):
            store.redeem("abc", _create())
    store.redeem(item.code, _create())
    # Con `other` aún abierto, la cuenta empieza de nuevo: un fallo más no lo quema.
    with pytest.raises(InvalidCode) as exc:
        store.redeem("abc", _create())
    assert not exc.value.burned
    assert store.get(other.id).state == "open"


def test_create_failure_keeps_code_open(store):
    item = store.open({})

    def boom(template: dict) -> ClientRecord:
        raise RuntimeError("db")

    for _ in range(pairing.MAX_FAILED):
        with pytest.raises(RuntimeError):
            store.redeem(item.code, boom)
    assert store.get(item.id).state == "open"
    store.redeem(item.code, _create())  # no contó como fallo ni lo quemó


def test_cancel(store):
    item = store.open({})
    assert store.cancel(item.id)
    assert store.get(item.id).state == "cancelled"
    assert not store.cancel(item.id)
    assert not store.cancel("nope")


def test_closed_codes_forgotten_after_ttl(store, clock):
    item = store.open({})
    store.cancel(item.id)
    clock.now += pairing.CODE_TTL_S
    assert store.get(item.id) is not None
    clock.now += 1
    assert store.get(item.id) is None
