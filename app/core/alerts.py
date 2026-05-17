"""Alertas Proativos — Feature 4 (V2).

Roda periodicamente em background (asyncio task iniciada pelo lifespan da
FastAPI) e gera alertas operacionais baseados em heuristicas sobre dados
reais coletados via tools do registry. Os alertas sao guardados em cache
in-memory e expostos via GET /api/v1/alerts para o frontend consumir.

Heuristicas aplicadas:
- ALTO: pedidos PENDENTES com entrega nas proximas 48h.
- MEDIO: producao parada (massas/recheios pendentes) com volume relevante.
- MEDIO: % de cancelamento alto vs concluidos (>= 20%).
- BAIXO: fornada da vez com >= 80% do estoque vendido.

Por ser heuristico (nao usa Gemini), e barato e nao consome quota.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Iterable

from app.config import settings
from app.core.cache import cache
from app.tools.registry import execute_tool

logger = logging.getLogger(__name__)

ALERTS_CACHE_KEY = "alerts:proactive"
ALERTS_TTL_SECONDS = 30 * 60  # cache de 30 min — refresh continua acontecendo
DEFAULT_REFRESH_SECONDS = 30 * 60  # task gera alertas a cada 30 min
INITIAL_DELAY_SECONDS = 15  # espera curta apos boot pra nao concorrer com startup

_PRIORITIES = ("high", "medium", "low")


@dataclass
class Alert:
    type: str  # "production" | "delivery" | "cancellation" | "batch"
    priority: str  # "high" | "medium" | "low"
    title: str
    message: str
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "priority": self.priority,
            "title": self.title,
            "message": self.message,
            "metadata": self.metadata,
        }


def _safe_int(value, default=0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _has_error(payload) -> bool:
    return isinstance(payload, dict) and ("error" in payload)


async def _gather(token: str | None) -> dict:
    """Chama em paralelo as tools que alimentam as heuristicas."""
    base_url = settings.carambolos_api_url
    calls = {
        "upcoming": execute_tool(
            "get_upcoming_deliveries", {"days_ahead": 2}, base_url, token
        ),
        "doughs": execute_tool("get_pending_doughs", {}, base_url, token),
        "fillings": execute_tool("get_pending_fillings", {}, base_url, token),
        "orders_count": execute_tool("get_orders_count", {}, base_url, token),
        "latest_batch": execute_tool("get_latest_batch_kpi", {}, base_url, token),
    }
    keys = list(calls.keys())
    results = await asyncio.gather(*calls.values(), return_exceptions=True)
    out = {}
    for key, value in zip(keys, results):
        if isinstance(value, Exception):
            logger.warning("Alerts: tool %s falhou: %s", key, value)
            out[key] = {"error": str(value)}
        else:
            out[key] = value
    return out


def _build_delivery_alert(upcoming) -> Alert | None:
    if _has_error(upcoming):
        return None
    items: Iterable = []
    if isinstance(upcoming, dict):
        items = upcoming.get("data") or []
    elif isinstance(upcoming, list):
        items = upcoming
    pendentes = [
        i for i in items
        if isinstance(i, dict)
        and str(i.get("status", "")).upper() == "PENDENTE"
    ]
    n = len(pendentes)
    if n == 0:
        return None
    return Alert(
        type="delivery",
        priority="high",
        title=f"{n} pedido(s) PENDENTE(s) com entrega proxima",
        message=(
            f"Voce tem {n} pedido(s) com entrega nas proximas 48 horas que "
            "ainda estao no status PENDENTE. Priorize cobranca/producao."
        ),
        metadata={"count": n},
    )


def _build_production_alert(doughs, fillings) -> Alert | None:
    pending_total = 0
    for payload in (doughs, fillings):
        if _has_error(payload):
            continue
        items = payload.get("data") if isinstance(payload, dict) else payload
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    pending_total += _safe_int(
                        item.get("quantidade") or item.get("qtd") or item.get("count"), 0
                    )
    if pending_total < 5:
        return None
    return Alert(
        type="production",
        priority="medium",
        title=f"{pending_total} item(ns) na fila de producao",
        message=(
            f"Existem {pending_total} massas/recheios aguardando producao. "
            "Revise a fila para evitar atrasos nos pedidos."
        ),
        metadata={"pending_total": pending_total},
    )


def _build_cancellation_alert(orders_count) -> Alert | None:
    if not isinstance(orders_count, dict) or _has_error(orders_count):
        return None
    cancelled = _safe_int(orders_count.get("CANCELADO"))
    completed = _safe_int(orders_count.get("CONCLUIDO"))
    total_finalized = cancelled + completed
    if total_finalized < 5:
        return None
    rate = cancelled / total_finalized
    if rate < 0.20:
        return None
    return Alert(
        type="cancellation",
        priority="high",
        title=f"Taxa de cancelamento em {rate:.0%}",
        message=(
            f"{cancelled} cancelamentos de {total_finalized} pedidos finalizados "
            f"({rate:.0%}). Investigar causas (atraso de entrega, falta de "
            "estoque, falha no atendimento)."
        ),
        metadata={"cancelled": cancelled, "completed": completed, "rate": round(rate, 2)},
    )


def _build_batch_alert(latest_batch) -> Alert | None:
    if not isinstance(latest_batch, dict) or _has_error(latest_batch):
        return None
    aproveitamento = latest_batch.get("aproveitamento")
    if aproveitamento is None:
        return None
    try:
        pct = float(aproveitamento)
    except (TypeError, ValueError):
        return None
    # backend pode mandar 0-1 ou 0-100
    if pct <= 1:
        pct *= 100
    if pct < 80:
        return None
    return Alert(
        type="batch",
        priority="low" if pct < 95 else "medium",
        title=f"Fornada com {pct:.0f}% do estoque vendido",
        message=(
            "A fornada mais recente ja vendeu a maior parte do estoque "
            f"({pct:.0f}%). Considere planejar uma reposicao."
        ),
        metadata={"aproveitamento_pct": round(pct, 1)},
    )


def _sort_by_priority(alerts: list[Alert]) -> list[Alert]:
    order = {p: i for i, p in enumerate(_PRIORITIES)}
    return sorted(alerts, key=lambda a: order.get(a.priority, 99))


async def compute_alerts(token: str | None = None) -> list[Alert]:
    data = await _gather(token)
    alerts: list[Alert] = []
    for builder, payload in (
        (_build_delivery_alert, data["upcoming"]),
        (_build_cancellation_alert, data["orders_count"]),
        (_build_batch_alert, data["latest_batch"]),
    ):
        a = builder(payload)
        if a is not None:
            alerts.append(a)
    prod = _build_production_alert(data["doughs"], data["fillings"])
    if prod is not None:
        alerts.append(prod)
    return _sort_by_priority(alerts)


def get_cached_alerts() -> dict | None:
    return cache.get(ALERTS_CACHE_KEY)


def _store_alerts(alerts: list[Alert]) -> dict:
    payload = {
        "generated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "alerts": [a.to_dict() for a in alerts],
    }
    cache.set(ALERTS_CACHE_KEY, payload, ttl=ALERTS_TTL_SECONDS)
    return payload


async def refresh_alerts_now(token: str | None = None) -> dict:
    """Forca o recalculo dos alertas (sem esperar o ciclo). Usado pelo endpoint."""
    alerts = await compute_alerts(token=token)
    payload = _store_alerts(alerts)
    logger.info("Alerts: %d alerta(s) gerado(s) (refresh sob demanda).", len(alerts))
    return payload


async def _periodic_loop(refresh_seconds: int):
    await asyncio.sleep(INITIAL_DELAY_SECONDS)
    while True:
        try:
            alerts = await compute_alerts(token=None)
            _store_alerts(alerts)
            logger.info("Alerts: %d alerta(s) gerado(s) pelo job periodico.", len(alerts))
        except asyncio.CancelledError:
            logger.info("Alerts: loop cancelado, encerrando.")
            raise
        except Exception as exc:  # noqa: BLE001 — engole pra task nao morrer
            logger.warning("Alerts: erro ao gerar alertas (%s). Tentara de novo.", exc)
        await asyncio.sleep(refresh_seconds)


_task_ref: asyncio.Task | None = None


def start_background_task(refresh_seconds: int = DEFAULT_REFRESH_SECONDS) -> asyncio.Task:
    """Inicia (ou retorna) a task que mantem os alertas atualizados."""
    global _task_ref
    if _task_ref is not None and not _task_ref.done():
        return _task_ref
    loop = asyncio.get_event_loop()
    _task_ref = loop.create_task(_periodic_loop(refresh_seconds))
    return _task_ref


async def stop_background_task() -> None:
    global _task_ref
    if _task_ref is None:
        return
    _task_ref.cancel()
    try:
        await _task_ref
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _task_ref = None
