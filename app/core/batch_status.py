"""Rotulos de fornada: status no sistema vs periodo no calendario."""

from __future__ import annotations

from datetime import date
from typing import Any


def _format_date_br(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    parts = text[:10].split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        y, m, d = parts
        return f"{d}/{m}/{y}"
    return text[:10]


def parse_batch_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if "T" in text:
        text = text.split("T", 1)[0]
    text = text[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def calendar_phase(
    start: date | None,
    end: date | None,
    *,
    today: date | None = None,
) -> str:
    """futuro | em_curso | passado | indefinido"""
    ref = today or date.today()
    if start and start > ref:
        return "futuro"
    if end and end < ref:
        return "passado"
    if start and start <= ref and (end is None or end >= ref):
        return "em_curso"
    return "indefinido"


_CALENDAR_TEXTO = {
    "futuro": "o periodo ainda nao comecou pelo calendario",
    "em_curso": "o periodo esta em curso no calendario",
    "passado": "o periodo ja terminou no calendario",
    "indefinido": "periodo indefinido no calendario",
}


def build_batch_summary(batch: dict, *, today: date | None = None) -> dict[str, Any]:
    ref = today or date.today()
    start_raw = batch.get("dataInicio") or batch.get("data_inicio")
    end_raw = batch.get("dataFim") or batch.get("data_fim")
    start = parse_batch_date(start_raw)
    end = parse_batch_date(end_raw)
    phase = calendar_phase(start, end, today=ref)
    aberta = batch.get("ativo") is not False

    di = _format_date_br(start_raw) or "?"
    df = _format_date_br(end_raw) or "?"
    fid = batch.get("id")

    sistema = "aberta no sistema (nao encerrada)" if aberta else "encerrada no sistema"
    cal_texto = _CALENDAR_TEXTO[phase]

    return {
        "numero": fid,
        "data_inicio": di,
        "data_fim": df,
        "status_sistema": "aberta" if aberta else "encerrada",
        "status_sistema_texto": sistema,
        "periodo_calendario": phase,
        "periodo_calendario_texto": cal_texto,
    }


def next_batch_instruction() -> str:
    return (
        "Proxima fornada agendada = fornada ABERTA no sistema cuja data de inicio "
        "e futura. NUNCA diga so 'esta ativa' ou 'em andamento' — isso confunde com "
        "producao rolando agora. Sempre diga: (1) aberta ou encerrada no sistema; "
        "(2) periodo DD/MM a DD/MM; (3) se o periodo ja comecou, esta em curso ou "
        "ainda vai comecar (use periodo_calendario_texto)."
    )


def active_batch_instruction() -> str:
    return (
        "Fornada operacional aberta no app (pedidos e produtos caem nela). "
        "Separe status no sistema e periodo no calendario — nao use 'ativa' como "
        "sinonimo de 'rolando agora'."
    )
