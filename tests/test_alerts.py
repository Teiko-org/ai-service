"""Testes da Feature 4 — Alertas Proativos."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core import alerts as alerts_mod
from app.core.alerts import (
    Alert,
    _build_batch_alert,
    _build_cancellation_alert,
    _build_delivery_alert,
    _build_production_alert,
    _sort_by_priority,
    compute_alerts,
    get_cached_alerts,
)
from app.core.cache import cache
from app.core.limiter import limiter
from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_state():
    cache.invalidate(alerts_mod.ALERTS_CACHE_KEY)
    limiter.reset()
    yield
    cache.invalidate(alerts_mod.ALERTS_CACHE_KEY)
    limiter.reset()


def test_delivery_alert_only_pending_orders():
    upcoming = {
        "data": [
            {"id": 1, "status": "PENDENTE"},
            {"id": 2, "status": "PAGO"},
            {"id": 3, "status": "PENDENTE"},
        ]
    }
    a = _build_delivery_alert(upcoming)
    assert a is not None
    assert a.priority == "high"
    assert "2" in a.title or "PENDENTE" in a.title


def test_delivery_alert_returns_none_when_no_pending():
    a = _build_delivery_alert({"data": [{"status": "PAGO"}]})
    assert a is None


def test_production_alert_threshold():
    doughs = {"data": [{"quantidade": 3}, {"quantidade": 1}]}
    fillings = {"data": [{"quantidade": 1}]}
    a = _build_production_alert(doughs, fillings)
    assert a is not None
    assert a.metadata["pending_total"] == 5


def test_production_alert_below_threshold_returns_none():
    a = _build_production_alert({"data": [{"quantidade": 1}]}, {"data": []})
    assert a is None


def test_cancellation_alert_high_rate():
    counts = {"CANCELADO": 5, "CONCLUIDO": 10}
    a = _build_cancellation_alert(counts)
    assert a is not None
    assert a.priority == "high"
    assert a.metadata["rate"] == round(5 / 15, 2)


def test_cancellation_alert_low_volume_returns_none():
    a = _build_cancellation_alert({"CANCELADO": 1, "CONCLUIDO": 1})
    assert a is None


def test_batch_alert_normalizes_percentage_units():
    a_pct = _build_batch_alert({"aproveitamento": 85})
    a_dec = _build_batch_alert({"aproveitamento": 0.85})
    assert a_pct is not None
    assert a_dec is not None
    assert a_pct.metadata["aproveitamento_pct"] == a_dec.metadata["aproveitamento_pct"]


def test_batch_alert_below_threshold_none():
    assert _build_batch_alert({"aproveitamento": 50}) is None


def test_sort_by_priority():
    items = [
        Alert("x", "low", "L", "..."),
        Alert("y", "high", "H", "..."),
        Alert("z", "medium", "M", "..."),
    ]
    sorted_alerts = _sort_by_priority(items)
    assert [a.priority for a in sorted_alerts] == ["high", "medium", "low"]


@pytest.mark.asyncio
async def test_compute_alerts_aggregates_payloads():
    upcoming = {"data": [{"status": "PENDENTE"}, {"status": "PENDENTE"}]}
    doughs = {"data": [{"quantidade": 6}]}
    fillings = {"data": []}
    orders_count = {"CANCELADO": 4, "CONCLUIDO": 6}
    latest_batch = {"aproveitamento": 90}

    async def fake_execute(name, *args, **kwargs):
        return {
            "get_upcoming_deliveries": upcoming,
            "get_pending_doughs": doughs,
            "get_pending_fillings": fillings,
            "get_orders_count": orders_count,
            "get_latest_batch_kpi": latest_batch,
        }[name]

    with patch("app.core.alerts.execute_tool", side_effect=fake_execute):
        result = await compute_alerts(token=None)

    types = {a.type for a in result}
    assert {"delivery", "cancellation", "batch", "production"} <= types
    assert all(a.priority in ("high", "medium", "low") for a in result)


def test_alerts_endpoint_returns_payload_and_caches():
    fake_payload = {
        "generated_at": "2026-05-09T00:00:00Z",
        "alerts": [
            {
                "type": "delivery",
                "priority": "high",
                "title": "T",
                "message": "M",
                "metadata": {},
            }
        ],
    }

    async def fake_refresh(token=None):
        cache.set(alerts_mod.ALERTS_CACHE_KEY, fake_payload, ttl=60)
        return fake_payload

    with patch("app.api.routes.refresh_alerts_now", side_effect=fake_refresh):
        resp = client.get("/api/v1/alerts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["generated_at"] == fake_payload["generated_at"]
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["title"] == "T"

    # segunda chamada usa cache (refresh nao deve ser chamado de novo)
    with patch("app.api.routes.refresh_alerts_now") as refresh:
        resp2 = client.get("/api/v1/alerts")
    assert resp2.status_code == 200
    refresh.assert_not_called()


def test_alerts_endpoint_refresh_param_bypasses_cache():
    cache.set(
        alerts_mod.ALERTS_CACHE_KEY,
        {"generated_at": "old", "alerts": []},
        ttl=60,
    )

    new_payload = {"generated_at": "new", "alerts": []}

    async def fake_refresh(token=None):
        cache.set(alerts_mod.ALERTS_CACHE_KEY, new_payload, ttl=60)
        return new_payload

    with patch("app.api.routes.refresh_alerts_now", side_effect=fake_refresh):
        resp = client.get("/api/v1/alerts?refresh=true")
    assert resp.status_code == 200
    assert resp.json()["generated_at"] == "new"


def test_get_cached_alerts_returns_none_when_empty():
    assert get_cached_alerts() is None
