from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.core import confirm_tokens, write_throttle
from app.core.cache import cache
from app.core.request_context import current_history, current_session_id
from app.tools.writes import pedido_bolo as pedido_tool


SESSION_ID = "sess-pedido-1"


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    monkeypatch.setattr(settings, "confirm_token_ttl_seconds", 120)
    yield


@pytest.fixture(autouse=True)
def _reset_stores():
    cache.clear()
    confirm_tokens._consumed_store = confirm_tokens._ConsumedTokensStore()
    confirm_tokens._issued_metadata_store = confirm_tokens._IssuedMetadataStore()
    write_throttle.write_throttle = write_throttle.WriteThrottle()
    yield
    cache.clear()


@pytest.fixture
def request_ctx():
    history = [{"role": "user", "content": "quero cadastrar um bolo"}]
    sess_tok = current_session_id.set(SESSION_ID)
    hist_tok = current_history.set(history)
    try:
        yield history
    finally:
        current_history.reset(hist_tok)
        current_session_id.reset(sess_tok)


def _delivery_date(offset: int = 3) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


def _base_args(**overrides) -> dict:
    args = {
        "massa_id": 1,
        "cobertura_id": 2,
        "formato": "CIRCULO",
        "tamanho": "TAMANHO_12",
        "recheio_unitario_id": 5,
        "nome_cliente": "Ana Silva",
        "telefone_cliente": "(11) 91234-5678",
        "tipo_entrega": "RETIRADA",
        "data_previsao_entrega": _delivery_date(),
        "horario_retirada": "17:00",
    }
    args.update(overrides)
    return args


def _chain_client(
    *,
    fail_at: str | None = None,
    coberturas: list | None = None,
):
    """Mock httpx client for the 4-step chain (+ optional GET cobertura)."""
    client = MagicMock()
    post_calls: list[tuple] = []
    delete_calls: list[str] = []

    async def _post(url, json=None, headers=None):
        post_calls.append((url, json))
        if fail_at == "recheio" and "recheio-pedido" in url:
            resp = MagicMock()
            resp.status_code = 400
            resp.json = MagicMock(return_value={"message": "recheio invalido"})
            resp.text = ""
            return resp
        if fail_at == "pedido" and url.endswith("/bolos/pedido"):
            resp = MagicMock()
            resp.status_code = 422
            resp.json = MagicMock(return_value={"message": "pedido rejeitado"})
            resp.text = ""
            return resp

        resp = MagicMock()
        resp.status_code = 201
        if "recheio-pedido" in url:
            resp.json = MagicMock(return_value={"id": 101})
        elif url.endswith("/bolos") and "pedido" not in url and "recheio" not in url:
            resp.json = MagicMock(return_value={"id": 202})
        elif url.endswith("/bolos/pedido"):
            resp.json = MagicMock(return_value={"id": 303})
        elif "resumo-pedido" in url:
            resp.json = MagicMock(return_value={"id": 404, "mensagem": "Pedido #404"})
        elif "enderecos" in url:
            resp.json = MagicMock(return_value={"id": 55})
        else:
            resp.json = MagicMock(return_value={"id": 1})
        resp.text = ""
        return resp

    async def _get(url, headers=None):
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value=coberturas if coberturas is not None else [])
        resp.text = ""
        return resp

    async def _delete(url, headers=None):
        delete_calls.append(url)
        resp = MagicMock()
        resp.status_code = 204
        return resp

    client.post = AsyncMock(side_effect=_post)
    client.get = AsyncMock(side_effect=_get)
    client.delete = AsyncMock(side_effect=_delete)
    client._post_calls = post_calls
    client._delete_calls = delete_calls
    return client


@pytest.mark.asyncio
async def test_preview_returns_token(request_ctx):
    client = _chain_client()
    result = await pedido_tool.execute(
        "create_pedido_bolo_full", _base_args(), "http://x", "tok", client
    )
    assert result["requires_confirmation"] is True
    assert result["action"] == "create_pedido_bolo_full"
    assert "confirm_token" in result
    assert result["payload"]["nome_cliente"] == "Ana Silva"
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_commit_retirada_full_chain(request_ctx):
    client = _chain_client()
    preview = await pedido_tool.execute(
        "create_pedido_bolo_full", _base_args(), "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim, confirma"})

    result = await pedido_tool.execute(
        "create_pedido_bolo_full",
        {
            **_base_args(),
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        "tok",
        client,
    )

    assert result["ok"] is True
    assert result["pedido_numero"] == 404
    assert result["ids_internos"]["resumo_pedido_id"] == 404
    assert client.post.await_count == 4
    pedido_call = client._post_calls[2]
    assert pedido_call[1]["horarioRetirada"] == "17:00"
    assert pedido_call[1]["tipoEntrega"] == "RETIRADA"


@pytest.mark.asyncio
async def test_commit_entrega_creates_address(request_ctx):
    client = _chain_client()
    args = _base_args(
        tipo_entrega="ENTREGA",
        horario_retirada=None,
        endereco={
            "cep": "01310100",
            "cidade": "Sao Paulo",
            "bairro": "Centro",
            "logradouro": "Av Paulista",
            "numero": "100",
        },
    )
    preview = await pedido_tool.execute(
        "create_pedido_bolo_full", args, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "pode criar"})

    result = await pedido_tool.execute(
        "create_pedido_bolo_full",
        {**args, "confirmed": True, "confirm_token": preview["confirm_token"]},
        "http://x",
        "tok",
        client,
    )

    assert result["ok"] is True
    assert client.post.await_count == 5
    assert client._post_calls[0][0].endswith("/enderecos")
    assert client._post_calls[3][1]["enderecoId"] == 55


@pytest.mark.asyncio
async def test_validation_entrega_without_address(request_ctx):
    client = _chain_client()
    args = _base_args(tipo_entrega="ENTREGA", horario_retirada=None)
    result = await pedido_tool.execute(
        "create_pedido_bolo_full", args, "http://x", "tok", client
    )
    assert "error" in result
    assert "endereco" in result["error"].lower()


@pytest.mark.asyncio
async def test_validation_missing_recheio(request_ctx):
    client = _chain_client()
    args = _base_args()
    del args["recheio_unitario_id"]
    result = await pedido_tool.execute(
        "create_pedido_bolo_full", args, "http://x", "tok", client
    )
    assert "error" in result
    assert "recheio" in result["error"].lower()


@pytest.mark.asyncio
async def test_commit_without_auth(request_ctx):
    client = _chain_client()
    preview = await pedido_tool.execute(
        "create_pedido_bolo_full", _base_args(), "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim"})
    result = await pedido_tool.execute(
        "create_pedido_bolo_full",
        {
            **_base_args(),
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        None,
        client,
    )
    assert "error" in result
    assert "autentic" in result["error"].lower()


@pytest.mark.asyncio
async def test_commit_same_turn_refused(request_ctx):
    client = _chain_client()
    preview = await pedido_tool.execute(
        "create_pedido_bolo_full", _base_args(), "http://x", "tok", client
    )
    result = await pedido_tool.execute(
        "create_pedido_bolo_full",
        {
            **_base_args(),
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        "tok",
        client,
    )
    assert "error" in result
    assert "explicita" in result["error"].lower()
    client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_rollback_on_pedido_failure(request_ctx):
    client = _chain_client(fail_at="pedido")
    preview = await pedido_tool.execute(
        "create_pedido_bolo_full", _base_args(), "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "sim"})

    result = await pedido_tool.execute(
        "create_pedido_bolo_full",
        {
            **_base_args(),
            "confirmed": True,
            "confirm_token": preview["confirm_token"],
        },
        "http://x",
        "tok",
        client,
    )

    assert "error" in result
    assert "422" in result["error"]
    assert any("/bolos/202" in u for u in client._delete_calls)
    assert any("/bolos/recheio-pedido/101" in u for u in client._delete_calls)


@pytest.mark.asyncio
async def test_resolves_cobertura_when_omitted(request_ctx):
    client = _chain_client(coberturas=[{"id": 9, "cor": "Rosa"}])
    args = _base_args()
    del args["cobertura_id"]
    preview = await pedido_tool.execute(
        "create_pedido_bolo_full", args, "http://x", "tok", client
    )
    request_ctx.append({"role": "user", "content": "ok"})
    await pedido_tool.execute(
        "create_pedido_bolo_full",
        {**args, "confirmed": True, "confirm_token": preview["confirm_token"]},
        "http://x",
        "tok",
        client,
    )
    bolo_call = client._post_calls[1]
    assert bolo_call[1]["coberturaId"] == 9
    client.get.assert_awaited_once()
