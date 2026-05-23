"""Resolve IDs reais perguntando ao assistant antes do roteiro fixo."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Protocol


class AskFn(Protocol):
    def __call__(self, question: str) -> dict: ...


@dataclass
class ParsedOrder:
    order_id: int
    tipo: str | None = None
    status: str | None = None
    delivery_date: str | None = None


_PEDIDO_NUM = re.compile(r"Pedido\s*#(\d+)", re.IGNORECASE)
_TIPO = re.compile(r"\((Bolo|Fornada)\)", re.IGNORECASE)
_DATE_BR = re.compile(r"(\d{2}/\d{2}/\d{4})")
_STATUS = re.compile(
    r"·\s*(Pendente|Pago|Concluido|Cancelado)\b", re.IGNORECASE
)
_MASSA_ID = re.compile(r"#\s*(\d+)\s*[·\-]")
_FORNADA_NUM = re.compile(r"[Ff]ornada\s*#?\s*(\d+)")
_DATE_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DATE_BR_PARTS = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")


def br_date_to_iso(value: str) -> str | None:
    """Converte dd/MM/yyyy para AAAA-MM-DD (ou devolve ISO ja valido)."""
    s = (value or "").strip()
    m = _DATE_BR_PARTS.fullmatch(s)
    if m:
        d, mo, y = m.group(1), m.group(2), m.group(3)
        return f"{y}-{mo}-{d}"
    if _DATE_ISO.match(s):
        return s
    return None


def _parse_orders_from_text(text: str) -> list[ParsedOrder]:
    if not text:
        return []
    rows: list[ParsedOrder] = []
    for line in text.splitlines():
        m = _PEDIDO_NUM.search(line)
        if not m:
            continue
        oid = int(m.group(1))
        tipo_m = _TIPO.search(line)
        status_m = _STATUS.search(line)
        date_m = _DATE_BR.search(line)
        rows.append(
            ParsedOrder(
                order_id=oid,
                tipo=tipo_m.group(1).capitalize() if tipo_m else None,
                status=status_m.group(1).capitalize() if status_m else None,
                delivery_date=date_m.group(1) if date_m else None,
            )
        )
    seen: set[int] = set()
    unique: list[ParsedOrder] = []
    for row in rows:
        if row.order_id in seen:
            continue
        seen.add(row.order_id)
        unique.append(row)
    return unique


def _first_matching(
    orders: list[ParsedOrder],
    *,
    tipo: str | None = None,
    status: str | None = None,
) -> ParsedOrder | None:
    for o in orders:
        if tipo and (o.tipo or "").lower() != tipo.lower():
            continue
        if status and (o.status or "").lower() != status.lower():
            continue
        return o
    return None


def _pick_mass_id(text: str) -> int | None:
    for line in text.splitlines():
        m = _MASSA_ID.search(line)
        if m:
            return int(m.group(1))
    return None


def _pick_batch_id(text: str) -> int | None:
    ids = _pick_batch_ids(text, limit=1)
    return ids[0] if ids else None


def _pick_batch_ids(text: str, *, limit: int = 5) -> list[int]:
    found: list[int] = []
    for m in _FORNADA_NUM.finditer(text):
        n = int(m.group(1))
        if n not in found:
            found.append(n)
        if len(found) >= limit:
            break
    return found


def _pick_massa_nome(text: str) -> str | None:
    for line in text.splitlines():
        m = re.search(r"#\s*\d+\s*[·\-]\s*(.+)", line)
        if m:
            return m.group(1).strip()
    return None


def build_runtime_config(
    orders: list[ParsedOrder],
    massa_id: int | None,
    batch_id: int | None,
    *,
    batch_ids: list[int] | None = None,
    massa_nome: str | None = None,
    delivery_fallback: str | None = None,
) -> dict:
    """Monta dict compativel com apply_config do roteiro_parser."""
    cfg: dict = {}
    if not orders:
        return cfg

    cfg["order_id"] = orders[0].order_id

    bolo = _first_matching(orders, tipo="Bolo")
    if bolo:
        cfg["order_id_bolo"] = bolo.order_id

    fornada = _first_matching(orders, tipo="Fornada")
    if fornada:
        cfg["order_id_fornada"] = fornada.order_id

    pendente = _first_matching(orders, status="Pendente")
    if pendente:
        cfg["order_id_pay"] = pendente.order_id
    else:
        cfg["order_id_pay"] = orders[0].order_id

    outros = [o for o in orders if o.order_id != cfg.get("order_id_pay")]
    if outros:
        cfg["order_id_cancel"] = outros[0].order_id
    elif len(orders) > 1:
        cfg["order_id_cancel"] = orders[1].order_id

    if len(orders) >= 2:
        cfg["order_whatsapp_1"] = orders[0].order_id
        cfg["order_whatsapp_2"] = orders[1].order_id
    elif len(orders) == 1:
        cfg["order_whatsapp_1"] = orders[0].order_id
        cfg["order_whatsapp_2"] = orders[0].order_id

    max_id = max(o.order_id for o in orders)
    cfg["order_id_fake"] = max_id + 50_000

    for o in orders:
        if o.delivery_date:
            cfg["delivery_date"] = o.delivery_date
            iso = br_date_to_iso(o.delivery_date)
            if iso:
                cfg["delivery_date_iso"] = iso
            break
    if delivery_fallback and "delivery_date" not in cfg:
        cfg["delivery_date"] = delivery_fallback
        iso = br_date_to_iso(delivery_fallback)
        if iso:
            cfg["delivery_date_iso"] = iso
    elif "delivery_date" in cfg and "delivery_date_iso" not in cfg:
        iso = br_date_to_iso(str(cfg["delivery_date"]))
        if iso:
            cfg["delivery_date_iso"] = iso

    if massa_id is not None:
        cfg["massa_id"] = massa_id
    if batch_id is not None:
        cfg["batch_id"] = batch_id

    if batch_ids:
        cfg["batch_close_1"] = batch_ids[0]
        cfg["batch_close_2"] = batch_ids[1] if len(batch_ids) > 1 else batch_ids[0]

    if massa_nome:
        cfg["massa_nome"] = massa_nome

    if "delivery_date" in cfg:
        cfg.setdefault("pedido_retirada_data", cfg["delivery_date"])

    di = date.today() + timedelta(days=30)
    df = di + timedelta(days=6)
    cfg.setdefault("fornada_inicio", di.strftime("%d/%m/%Y"))
    cfg.setdefault("fornada_fim", df.strftime("%d/%m/%Y"))
    di2 = date.today() + timedelta(days=37)
    df2 = di2 + timedelta(days=6)
    cfg.setdefault("fornada_substituir_inicio", di2.strftime("%d/%m/%Y"))
    cfg.setdefault("fornada_substituir_fim", df2.strftime("%d/%m/%Y"))

    return cfg


@dataclass
class ProbeResult:
    key: str
    question: str
    answer_preview: str
    ok: bool


def resolve_ids_from_assistant(
    ask: AskFn,
    *,
    delay_s: float = 0.0,
    on_probe: Callable[[ProbeResult], None] | None = None,
) -> tuple[dict, list[ProbeResult]]:
    """
    Faz perguntas de listagem e extrai IDs para o roteiro.
    Nao altera o banco — so leitura.
    """
    import time

    probes: list[tuple[str, str]] = [
        ("orders_recent", "Quais foram os pedidos mais recentes?"),
        ("orders_pending", "Lista os pedidos com status PENDENTE"),
        ("massas", "Lista as massas cadastradas com id e nome"),
        ("batch_active", "Mostra a fornada ativa e os produtos"),
        (
            "batches_list",
            "Lista todas as fornadas ativas com o id de cada uma",
        ),
    ]

    all_orders: list[ParsedOrder] = []
    massa_id: int | None = None
    massa_nome: str | None = None
    batch_id: int | None = None
    all_batch_ids: list[int] = []
    log: list[ProbeResult] = []

    for key, question in probes:
        if delay_s > 0 and log:
            time.sleep(delay_s)
        try:
            data = ask(question)
            answer = (data.get("answer") or "").strip()
            ok = bool(answer) and "nao foi possivel gerar" not in answer.lower()
            preview = answer.replace("\n", " ")[:200]
            log.append(ProbeResult(key, question, preview, ok))
            if on_probe:
                on_probe(log[-1])

            if key.startswith("orders"):
                all_orders.extend(_parse_orders_from_text(answer))
            elif key == "massas":
                massa_id = _pick_mass_id(answer)
                massa_nome = _pick_massa_nome(answer) or massa_nome
            elif key in ("batch_active", "batches_list"):
                ids = _pick_batch_ids(answer)
                all_batch_ids.extend(ids)
                if key == "batch_active" and ids:
                    batch_id = ids[0]
        except Exception as exc:  # noqa: BLE001
            log.append(ProbeResult(key, question, str(exc)[:200], False))
            if on_probe:
                on_probe(log[-1])

    seen: set[int] = set()
    deduped: list[ParsedOrder] = []
    for o in all_orders:
        if o.order_id in seen:
            continue
        seen.add(o.order_id)
        deduped.append(o)

    seen_batch: set[int] = set()
    dedup_batches: list[int] = []
    for bid in all_batch_ids:
        if bid in seen_batch:
            continue
        seen_batch.add(bid)
        dedup_batches.append(bid)
    if batch_id is None and dedup_batches:
        batch_id = dedup_batches[0]

    runtime = build_runtime_config(
        deduped,
        massa_id,
        batch_id,
        batch_ids=dedup_batches or None,
        massa_nome=massa_nome,
    )
    return runtime, log
