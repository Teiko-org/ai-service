import pytest

from app.core.write_throttle import WriteThrottle, WriteThrottleError


def test_under_limit_allows():
    t = WriteThrottle(limits=((3, 60),))
    for _ in range(3):
        t.check("sess-1", "create_batch")


def test_over_limit_rejects():
    t = WriteThrottle(limits=((3, 60),))
    for _ in range(3):
        t.check("sess-1", "create_batch")
    with pytest.raises(WriteThrottleError, match="Muitas confirmacoes"):
        t.check("sess-1", "create_batch")


def test_separate_session_independent():
    t = WriteThrottle(limits=((2, 60),))
    t.check("sess-A", "create_batch")
    t.check("sess-A", "create_batch")
    t.check("sess-B", "create_batch")
    with pytest.raises(WriteThrottleError):
        t.check("sess-A", "create_batch")
    t.check("sess-B", "create_batch")


def test_separate_tool_independent():
    t = WriteThrottle(limits=((2, 60),))
    t.check("sess-1", "create_batch")
    t.check("sess-1", "create_batch")
    t.check("sess-1", "create_pedido_bolo_full")
    with pytest.raises(WriteThrottleError):
        t.check("sess-1", "create_batch")


def test_sliding_window_recovers(monkeypatch):
    import app.core.write_throttle as wt_mod

    fake_now = [1000.0]
    monkeypatch.setattr(wt_mod.time, "time", lambda: fake_now[0])

    t = WriteThrottle(limits=((2, 60),))
    t.check("sess-1", "create_batch")
    t.check("sess-1", "create_batch")
    with pytest.raises(WriteThrottleError):
        t.check("sess-1", "create_batch")

    fake_now[0] += 61
    t.check("sess-1", "create_batch")


def test_anonymous_session_allowed_and_limited():
    t = WriteThrottle(limits=((1, 60),))
    t.check(None, "create_batch")
    with pytest.raises(WriteThrottleError):
        t.check(None, "create_batch")
