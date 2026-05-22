from app.core.alerts import Alert, _dedupe_and_cap, _sort_by_priority


def test_dedupe_removes_duplicate_type_title():
    alerts = [
        Alert("delivery", "high", "3 pedidos", "msg"),
        Alert("delivery", "high", "3 pedidos", "dup"),
        Alert("batch", "low", "Fornada cheia", "msg2"),
    ]
    out = _dedupe_and_cap(_sort_by_priority(alerts), max_items=5)
    assert len(out) == 2
    assert out[0].type == "delivery"
    assert out[1].type == "batch"


def test_cap_limits_total():
    alerts = [
        Alert("delivery", "high", f"Titulo {i}", "m") for i in range(10)
    ]
    out = _dedupe_and_cap(alerts, max_items=3)
    assert len(out) == 3
