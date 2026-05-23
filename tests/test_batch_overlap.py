from datetime import date

import pytest

from app.tools.writes.batch_overlap import (
    _periods_overlap,
    _pick_active_for_message,
    active_batch_error_message,
)


def test_periods_overlap_true():
    assert _periods_overlap(
        date(2026, 6, 1), date(2026, 6, 7), date(2026, 6, 5), date(2026, 6, 10)
    )


def test_periods_overlap_false():
    assert not _periods_overlap(
        date(2026, 6, 1), date(2026, 6, 7), date(2026, 6, 8), date(2026, 6, 15)
    )


def test_pick_active_prefers_latest_data_fim():
    items = [
        {"id": 1, "ativo": True, "dataFim": "2026-06-07"},
        {"id": 2, "ativo": True, "dataFim": "2026-06-20"},
    ]
    picked = _pick_active_for_message(items)
    assert picked["id"] == 2


def test_active_batch_message_mentions_close():
    msg = active_batch_error_message(
        {"id": 11, "dataInicio": "2026-06-01", "dataFim": "2026-06-07"}
    )
    assert "11" in msg
    assert "replace_active_batch" in msg
    assert "fornada ativa" in msg.lower()
