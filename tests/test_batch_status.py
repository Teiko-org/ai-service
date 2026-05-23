"""Status de fornada: sistema vs calendario."""

from datetime import date

from app.core.batch_status import build_batch_summary, calendar_phase, parse_batch_date


def test_calendar_phase_futuro():
    ref = date(2026, 5, 23)
    start = date(2026, 6, 10)
    end = date(2026, 6, 16)
    assert calendar_phase(start, end, today=ref) == "futuro"


def test_calendar_phase_em_curso():
    ref = date(2026, 6, 12)
    start = date(2026, 6, 10)
    end = date(2026, 6, 16)
    assert calendar_phase(start, end, today=ref) == "em_curso"


def test_build_batch_summary_futuro_aberta():
    batch = {
        "id": 14,
        "dataInicio": "2026-06-10",
        "dataFim": "2026-06-16",
        "ativo": True,
    }
    summary = build_batch_summary(batch, today=date(2026, 5, 23))
    assert summary["numero"] == 14
    assert summary["status_sistema"] == "aberta"
    assert summary["periodo_calendario"] == "futuro"
    assert "ainda nao comecou" in summary["periodo_calendario_texto"]
    assert summary["data_inicio"] == "10/06/2026"


def test_parse_batch_date_iso():
    assert parse_batch_date("2026-06-10T00:00:00") == date(2026, 6, 10)
