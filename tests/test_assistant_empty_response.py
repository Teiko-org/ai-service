"""Resposta Gemini 200 sem texto: nudge e extracao via response.text."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.assistant import CarambolosAssistant


def _text_part(text: str):
    part = MagicMock()
    part.text = text
    part.thought = False
    part.function_call = None
    return part


def _function_call_part(name: str, args: dict | None = None):
    part = MagicMock()
    part.text = None
    part.thought = False
    fc = MagicMock()
    fc.name = name
    fc.args = args or {}
    part.function_call = fc
    return part


def _make_response(parts, sdk_text=None):
    response = MagicMock()
    candidate = MagicMock()
    candidate.content.parts = parts
    candidate.finish_reason = "STOP"
    response.candidates = [candidate]
    response.text = sdk_text
    return response


@pytest.mark.asyncio
async def test_empty_first_response_retries_with_nudge_and_calls_tool():
    empty = _make_response([], sdk_text=None)
    with_tool = _make_response(
        [_function_call_part("get_orders_by_dough", {"dough_id": 2})]
    )
    final = _make_response(
        [_text_part("Pedidos da massa 2: #10 e #20.")],
        sdk_text="Pedidos da massa 2: #10 e #20.",
    )

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[empty, with_tool, final]
        )
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": [{"id": 10}]}

        a = CarambolosAssistant()
        result = await a.ask("Quais pedidos usam a massa com id 2?")

    assert "massa 2" in result["answer"]
    assert "get_orders_by_dough" in result["tools_used"]
    assert mock_client.aio.models.generate_content.await_count == 3
