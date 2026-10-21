from src.services.stats import GatewayStats, gateway_stats


def test_stats_counts_and_snapshot():
    s = GatewayStats()
    s.session_opened()
    s.session_opened()
    s.session_closed("normal")
    s.handshake_rejected("bad_key")
    s.turn_started("normal")
    s.turn_started("push")
    s.turn_started("push")
    s.turn_error("turn_timeout")
    assert s.snapshot() == {
        "sessions_opened": 2,
        "sessions_closed": {"normal": 1},
        "handshake_rejected": {"bad_key": 1},
        "turns": {"normal": 1, "push": 2},
        "turn_errors": {"turn_timeout": 1},
    }
    assert type(s.snapshot()["turns"]) is dict


def test_reset_zeroes():
    s = GatewayStats()
    s.session_opened()
    s.session_closed("x")
    s.handshake_rejected("y")
    s.turn_started("normal")
    s.turn_error("z")
    s.reset()
    assert s.snapshot() == {
        "sessions_opened": 0,
        "sessions_closed": {},
        "handshake_rejected": {},
        "turns": {"normal": 0, "push": 0},
        "turn_errors": {},
    }


def test_singleton_exists():
    assert isinstance(gateway_stats, GatewayStats)
