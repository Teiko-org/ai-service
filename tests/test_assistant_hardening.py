import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.assistant import (
    CarambolosAssistant,
    MAX_TOOL_ROUNDS_READ,
    MAX_TOOL_ROUNDS_WRITE,
    _is_write_tool,
)


def _text_part(text: str):
    part = MagicMock()
    part.text = text
    part.function_call = None
    return part


def _function_call_part(name: str, args: dict | None = None):
    part = MagicMock()
    part.text = None
    fc = MagicMock()
    fc.name = name
    fc.args = args or {}
    part.function_call = fc
    return part


def _make_response(parts):
    response = MagicMock()
    candidate = MagicMock()
    candidate.content.parts = parts
    response.candidates = [candidate]
    return response


def test_is_write_tool_recognizes_create_prefix():
    assert _is_write_tool("create_batch") is True
    assert _is_write_tool("add_batch_lines") is True
    assert _is_write_tool("update_pedido") is True
    assert _is_write_tool("delete_fornada") is True


def test_is_write_tool_recognizes_actions():
    assert _is_write_tool("mark_order_as_paid") is True
    assert _is_write_tool("mark_order_as_cancelled") is True


def test_is_write_tool_excludes_whatsapp_message():
    assert _is_write_tool("generate_whatsapp_message") is False


def test_is_write_tool_excludes_read_tools():
    assert _is_write_tool("get_orders_count") is False
    assert _is_write_tool("get_next_batch") is False
    assert _is_write_tool("get_registered_products") is False


@pytest.mark.asyncio
async def test_write_intent_extends_max_rounds():
    looping_write = _make_response([_function_call_part("create_batch")])

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=looping_write)
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"requires_confirmation": True}

        a = CarambolosAssistant()
        await a.ask("crie fornada")

    assert mock_exec.await_count == MAX_TOOL_ROUNDS_WRITE
    assert MAX_TOOL_ROUNDS_WRITE > MAX_TOOL_ROUNDS_READ


@pytest.mark.asyncio
async def test_read_only_stays_at_read_limit():
    looping_read = _make_response([_function_call_part("get_orders_count")])

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=looping_read)
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": {}}

        a = CarambolosAssistant()
        await a.ask("quantos pedidos?")

    assert mock_exec.await_count == MAX_TOOL_ROUNDS_READ


@pytest.mark.asyncio
async def test_tool_call_log_omits_arg_values(caplog):
    first = _make_response([
        _function_call_part(
            "get_cake_order_details",
            args={"order_id": 42, "telefone": "(11) 91234-5678"},
        )
    ])
    final = _make_response([_text_part("Pedido detalhado.")])

    with caplog.at_level(logging.INFO, logger="app.core.assistant"), \
         patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(side_effect=[first, final])
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"id": 42}

        a = CarambolosAssistant(auth_token="t")
        await a.ask("detalhe do pedido 42")

    call_lines = [
        rec.getMessage()
        for rec in caplog.records
        if "Chamando tool" in rec.getMessage()
    ]
    assert call_lines, "esperava log 'Chamando tool: ...'"
    line = call_lines[-1]

    assert "get_cake_order_details" in line
    assert "order_id" in line
    assert "telefone" in line

    assert "91234-5678" not in line
    assert "(11)" not in line
    assert ": 42" not in line and "=42" not in line
