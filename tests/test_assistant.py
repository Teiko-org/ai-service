"""Testes do CarambolosAssistant: tool calling, fallback de modelos, rate limit, recuperacao de erros."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.assistant import (
    CarambolosAssistant,
    RateLimitError,
    _extract_retry_seconds,
    MAX_TOOL_ROUNDS,
)


class FakeApiError(Exception):
    """Stand-in for genai.errors.APIError, mantém os mesmos atributos usados."""

    def __init__(self, code: int, message: str = "boom"):
        self.code = code
        self.message = message
        super().__init__(message)


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


def _patch_apierror():
    """Sostitui genai.errors.APIError pelo nosso FakeApiError nos pontos onde e usado."""
    return patch("app.core.assistant.genai.errors.APIError", FakeApiError)


# ============================================================
# _extract_retry_seconds
# ============================================================

def test_extract_retry_seconds_present():
    err = FakeApiError(429, "rate limited; retryDelay: 42s details...")
    assert _extract_retry_seconds(err) == 42.0


def test_extract_retry_seconds_decimal():
    err = FakeApiError(429, 'retryDelay": "12.5s"')
    assert _extract_retry_seconds(err) == 12.5


def test_extract_retry_seconds_missing():
    err = FakeApiError(429, "no retry information")
    assert _extract_retry_seconds(err) is None


# ============================================================
# ask() — caminho feliz sem tools
# ============================================================

@pytest.mark.asyncio
async def test_ask_returns_text_when_no_function_call():
    response = _make_response([_text_part("Tudo bem por aqui!")])

    with patch("app.core.assistant.get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=response)
        mock_client_factory.return_value = mock_client

        a = CarambolosAssistant()
        result = await a.ask("oi")

    assert result["answer"] == "Tudo bem por aqui!"
    assert result["tools_used"] == []


# ============================================================
# ask() — chama tool e responde com texto
# ============================================================

@pytest.mark.asyncio
async def test_ask_executes_tool_and_returns_final_answer():
    first = _make_response([_function_call_part("get_orders_count")])
    second = _make_response([_text_part("Voce tem 5 pedidos.")])

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(side_effect=[first, second])
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": {"PENDENTE": 5}}

        a = CarambolosAssistant(auth_token="t")
        result = await a.ask("quantos pedidos?")

    assert "5 pedidos" in result["answer"]
    assert result["tools_used"] == ["get_orders_count"]
    mock_exec.assert_awaited_once()


@pytest.mark.asyncio
async def test_ask_whatsapp_returns_api_text_without_model_wrapper():
    api_msg = "Ola Maria! Pedido #3019 confirmado."
    first = _make_response(
        [
            _function_call_part(
                "generate_whatsapp_message", {"order_ids": [3019, 1796]}
            )
        ]
    )
    second = _make_response(
        [
            _text_part(
                'Ok, segue o texto:\n\n"'
                + api_msg
                + '"'
            )
        ]
    )

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[first, second]
        )
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {
            "ok": True,
            "message_text": api_msg,
            "order_ids": [3019, 1796],
        }

        a = CarambolosAssistant(auth_token="t")
        result = await a.ask(
            "Gera mensagem WhatsApp para pedidos #3019 e #1796"
        )

    assert result["answer"] == api_msg
    assert "Segue o texto" not in result["answer"]
    assert result["tools_used"] == ["generate_whatsapp_message"]


# ============================================================
# ask() — protege contra loop infinito de tools
# ============================================================

@pytest.mark.asyncio
async def test_ask_respects_max_tool_rounds():
    looping = _make_response([_function_call_part("get_orders_count")])

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=looping)
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": {}}

        a = CarambolosAssistant()
        result = await a.ask("?")

    assert mock_exec.await_count <= MAX_TOOL_ROUNDS
    assert isinstance(result["tools_used"], list)


# ============================================================
# ask() — historico e enviado ao modelo
# ============================================================

@pytest.mark.asyncio
async def test_ask_includes_history_in_contents():
    response = _make_response([_text_part("Ok")])
    captured = {}

    async def capture(**kwargs):
        captured.update(kwargs)
        return response

    with patch("app.core.assistant.get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(side_effect=capture)
        mock_client_factory.return_value = mock_client

        a = CarambolosAssistant()
        await a.ask(
            "agora?",
            history=[
                {"role": "user", "content": "oi"},
                {"role": "assistant", "content": "ola!"},
            ],
        )

    contents = captured["contents"]
    assert len(contents) == 3  # 2 historico + 1 pergunta atual
    assert contents[0].role == "user"
    assert contents[1].role == "model"
    assert contents[2].role == "user"


# ============================================================
# Fallback: 429 troca de modelo
# ============================================================

@pytest.mark.asyncio
async def test_ask_fallback_on_429_uses_next_model():
    success = _make_response([_text_part("recuperado")])

    err_429 = FakeApiError(429, "rate limited")

    with _patch_apierror(), \
         patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.model_manager") as mock_mm:
        mock_mm.get_model.return_value = "model-a"
        mock_mm.mark_rate_limited.return_value = "model-b"

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[err_429, success]
        )
        mock_client_factory.return_value = mock_client

        a = CarambolosAssistant()
        result = await a.ask("oi")

    assert result["answer"] == "recuperado"
    mock_mm.mark_rate_limited.assert_called_once_with("model-a", None)


# ============================================================
# Todos os modelos esgotados -> RateLimitError
# ============================================================

@pytest.mark.asyncio
async def test_ask_raises_rate_limit_when_all_exhausted():
    err = FakeApiError(429, "rate limited")

    with _patch_apierror(), \
         patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.model_manager") as mock_mm, \
         patch("app.core.assistant.api_key_manager") as mock_km:
        mock_mm.get_model.return_value = "model-a"
        mock_mm.mark_rate_limited.side_effect = ["model-b", "model-a", None]
        mock_km.get_key_index.return_value = 0
        mock_km.mark_rate_limited.return_value = None

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(side_effect=err)
        mock_client_factory.return_value = mock_client

        a = CarambolosAssistant()
        with pytest.raises(RateLimitError):
            await a.ask("oi")


@pytest.mark.asyncio
async def test_ask_fallback_on_429_rotates_api_key_when_models_exhausted():
    success = _make_response([_text_part("com outra chave")])
    err_429 = FakeApiError(429, "rate limited")

    client_a = MagicMock()
    client_a.aio.models.generate_content = AsyncMock(side_effect=err_429)
    client_b = MagicMock()
    client_b.aio.models.generate_content = AsyncMock(return_value=success)

    with _patch_apierror(), \
         patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.model_manager") as mock_mm, \
         patch("app.core.assistant.api_key_manager") as mock_km:
        mock_km.get_key_index.return_value = 0
        mock_mm.get_model.return_value = "model-a"
        # model-a tried -> model-b tried -> model-a again -> rotate key
        mock_mm.mark_rate_limited.side_effect = ["model-b", "model-a"]
        mock_km.mark_rate_limited.return_value = 1
        mock_mm.get_model.side_effect = ["model-a", "model-a"]

        def _client_for_key(key_index=None):
            if key_index == 1:
                return client_b
            return client_a

        mock_client_factory.side_effect = _client_for_key

        a = CarambolosAssistant()
        result = await a.ask("oi")

    assert result["answer"] == "com outra chave"
    mock_km.mark_rate_limited.assert_called_once_with(0, None)


# ============================================================
# 404 (modelo invalido) tambem aciona fallback
# ============================================================

@pytest.mark.asyncio
async def test_ask_fallback_on_404_model_not_found():
    success = _make_response([_text_part("ok")])
    err_404 = FakeApiError(404, "model not found")

    with _patch_apierror(), \
         patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.model_manager") as mock_mm:
        mock_mm.get_model.return_value = "model-x"
        mock_mm.mark_rate_limited.return_value = "model-y"

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[err_404, success]
        )
        mock_client_factory.return_value = mock_client

        a = CarambolosAssistant()
        result = await a.ask("oi")

    assert result["answer"] == "ok"
    mock_mm.mark_rate_limited.assert_called_once()
    args, kwargs = mock_mm.mark_rate_limited.call_args
    assert args[0] == "model-x"
    assert args[1] == 3600  # cooldown longo para 404


# ============================================================
# Outros erros nao 429/404 sao reembrulhados
# ============================================================

@pytest.mark.asyncio
async def test_ask_other_api_error_raises_runtime():
    err = FakeApiError(500, "internal error")

    with _patch_apierror(), \
         patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.model_manager") as mock_mm:
        mock_mm.get_model.return_value = "m"

        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(side_effect=err)
        mock_client_factory.return_value = mock_client

        a = CarambolosAssistant()
        with pytest.raises(RuntimeError) as exc_info:
            await a.ask("oi")
    assert "Falha" in str(exc_info.value)


# ============================================================
# Resposta vazia retorna mensagem de fallback
# ============================================================

@pytest.mark.asyncio
async def test_ask_empty_response_fallback_text():
    empty = MagicMock()
    empty.candidates = []
    empty.text = None

    with patch("app.core.assistant.get_client") as mock_client_factory:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[empty, empty]
        )
        mock_client_factory.return_value = mock_client

        a = CarambolosAssistant()
        result = await a.ask("oi")
    assert "Nao foi possivel" in result["answer"]


@pytest.mark.asyncio
async def test_ask_recovery_when_final_text_empty_after_tool():
    first = _make_response([_function_call_part("get_recent_orders")])
    empty_final = _make_response([_text_part("")])
    recovered = _make_response(
        [_text_part("Clientes mais frequentes: Ana (3), Bruno (2).")]
    )

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(
            side_effect=[first, empty_final, recovered]
        )
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": [{"nomeCliente": "Ana"}, {"nomeCliente": "Ana"}]}

        a = CarambolosAssistant(auth_token="t")
        result = await a.ask("Quem sao os clientes que mais pediram?")

    assert "Ana" in result["answer"]
    assert result["tools_used"] == ["get_recent_orders"]
    assert mock_client.aio.models.generate_content.await_count == 3


# ============================================================
# generate_insights — parse JSON limpo
# ============================================================

@pytest.mark.asyncio
async def test_generate_insights_parses_json_array():
    raw = (
        '[{"type":"alert","priority":"high",'
        '"title":"Pedidos urgentes","message":"5 pedidos vencendo"}]'
    )
    response = _make_response([_text_part(raw)])

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=response)
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": []}

        a = CarambolosAssistant()
        insights = await a.generate_insights()

    assert len(insights) == 1
    assert insights[0]["type"] == "alert"


@pytest.mark.asyncio
async def test_generate_insights_strips_markdown_fence():
    raw = '```json\n[{"type":"trend","priority":"low","title":"x","message":"y"}]\n```'
    response = _make_response([_text_part(raw)])

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=response)
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": []}

        a = CarambolosAssistant()
        insights = await a.generate_insights()
    assert len(insights) == 1


@pytest.mark.asyncio
async def test_generate_insights_invalid_json_falls_back():
    response = _make_response([_text_part("nao e json")])

    with patch("app.core.assistant.get_client") as mock_client_factory, \
         patch("app.core.assistant.execute_tool", new_callable=AsyncMock) as mock_exec:
        mock_client = MagicMock()
        mock_client.aio.models.generate_content = AsyncMock(return_value=response)
        mock_client_factory.return_value = mock_client
        mock_exec.return_value = {"data": []}

        a = CarambolosAssistant()
        insights = await a.generate_insights()

    assert len(insights) == 1
    assert insights[0]["type"] == "trend"
    assert "nao e json" in insights[0]["message"]
