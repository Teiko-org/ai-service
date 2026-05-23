from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.tools.writes.batch_overlap import find_active_batch


@pytest.mark.asyncio
async def test_find_active_batch_returns_none_when_empty():
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(return_value=[])
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)

    assert await find_active_batch(client, "http://x", None) is None


@pytest.mark.asyncio
async def test_find_active_batch_returns_active():
    di = date.today() + timedelta(days=10)
    df = di + timedelta(days=6)
    resp = MagicMock()
    resp.status_code = 200
    resp.json = MagicMock(
        return_value=[
            {
                "id": 99,
                "dataInicio": di.isoformat(),
                "dataFim": df.isoformat(),
                "ativo": True,
            }
        ]
    )
    resp.raise_for_status = MagicMock()
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)

    hit = await find_active_batch(client, "http://x", None)
    assert hit is not None
    assert hit["id"] == 99
