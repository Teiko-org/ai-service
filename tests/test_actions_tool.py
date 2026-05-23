"""Testes da Feature 1 — Acoes via Chat (Agentic) com confirmacao two-step."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.tools.actions import (
    ACTION_TOOL_NAMES,
    DECLARATIONS as ACTION_DECLARATIONS,
)
from app.tools.registry import TOOL_DECLARATIONS, execute_tool


@pytest.fixture(autouse=True)
def _no_confirm_secret_by_default(monkeypatch):
    """Isola testes do CONFIRM_TOKEN_SECRET do .env local do desenvolvedor."""
    monkeypatch.setattr(settings, "confirm_token_secret", "")


def test_all_action_tools_registered():
    names = {d.name for d in TOOL_DECLARATIONS}
    for action_name in ACTION_TOOL_NAMES:
        assert action_name in names, f"Tool {action_name} nao registrada"


def test_action_declarations_have_confirmed_param():
    for decl in ACTION_DECLARATIONS:
        if decl.name == "generate_whatsapp_message":
            continue
        props = decl.parameters.properties
        assert "confirmed" in props, f"{decl.name} sem param confirmed"
        assert "order_id" in props, f"{decl.name} sem param order_id"


def _make_client(get_resp=None, patch_resp=None, post_resp=None):
    client = MagicMock()
    if get_resp is not None:
        client.get = AsyncMock(return_value=get_resp)
    if patch_resp is not None:
        client.patch = AsyncMock(return_value=patch_resp)
    if post_resp is not None:
        client.post = AsyncMock(return_value=post_resp)
    return client


def _resp(status_code=200, json_payload=None, text_payload=None):
    r = MagicMock()
    r.status_code = status_code
    r.json = MagicMock(return_value=json_payload or {})
    r.text = text_payload or ""
    r.raise_for_status = MagicMock()
    return r


@pytest.mark.asyncio
async def test_mark_paid_accepts_pedido_hash_string_order_id():
    """Modelo pode mandar order_id como string estilo #42 — normalizamos."""
    preview = {"id": 42, "status": "PENDENTE"}
    client = _make_client(get_resp=_resp(200, preview))

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_paid", {"order_id": "#42"}, "http://x", "tok"
        )

    assert result["requires_confirmation"] is True
    assert result["order_id"] == 42
    assert "/resumo-pedido/42" in client.get.call_args[0][0]


@pytest.mark.asyncio
async def test_mark_paid_without_confirm_returns_preview():
    preview = {"id": 42, "cliente": "Ana", "status": "PENDENTE", "valor": 150.0}
    client = _make_client(get_resp=_resp(200, preview))

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_paid", {"order_id": 42}, "http://x", "tok"
        )

    assert result["requires_confirmation"] is True
    assert result["target_status"] == "PAGO"
    assert result["order_id"] == 42
    assert result["preview"] == preview
    client.get.assert_called_once()
    assert "/resumo-pedido/42" in client.get.call_args[0][0]


@pytest.mark.asyncio
async def test_mark_paid_with_confirm_executes_patch():
    patched_payload = {"id": 42, "status": "PAGO"}
    client = _make_client(patch_resp=_resp(200, patched_payload))

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_paid",
            {"order_id": 42, "confirmed": True},
            "http://x",
            "tok",
        )

    assert result["ok"] is True
    assert result["new_status"] == "PAGO"
    assert result["data"] == patched_payload
    client.patch.assert_called_once()
    url = client.patch.call_args[0][0]
    assert url.endswith("/resumo-pedido/42/pago")


@pytest.mark.asyncio
async def test_mark_paid_without_order_id_returns_error():
    client = _make_client()
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_paid", {"confirmed": True}, "http://x", None
        )
    assert "error" in result


@pytest.mark.asyncio
async def test_action_404_propagates_human_message():
    client = _make_client(patch_resp=_resp(404))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_completed",
            {"order_id": 99, "confirmed": True},
            "http://x",
            "tok",
        )
    assert "error" in result
    assert "99" in result["error"]


@pytest.mark.asyncio
async def test_action_422_returns_invalid_transition_error():
    client = _make_client(patch_resp=_resp(422))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_cancelled",
            {"order_id": 5, "confirmed": True},
            "http://x",
            "tok",
        )
    assert "error" in result
    assert "CANCELADO" in result["error"]


@pytest.mark.asyncio
async def test_whatsapp_message_does_not_require_confirmation():
    text = "Mensagem consolidada bla bla"
    client = _make_client(post_resp=_resp(200, None, text_payload=text))

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "generate_whatsapp_message",
            {"order_ids": [1, 2, 3]},
            "http://x",
            "tok",
        )

    assert result["ok"] is True
    assert result["message_text"] == text
    assert result["order_ids"] == [1, 2, 3]
    client.post.assert_called_once()
    payload = client.post.call_args.kwargs["json"]
    assert payload == {"idsResumo": [1, 2, 3]}


@pytest.mark.asyncio
async def test_whatsapp_message_rejects_empty_ids():
    client = _make_client()
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "generate_whatsapp_message", {"order_ids": []}, "http://x", None
        )
    assert "error" in result


@pytest.mark.asyncio
async def test_preview_handles_404_gracefully():
    client = _make_client(get_resp=_resp(404))
    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_pending", {"order_id": 999}, "http://x", "tok"
        )
    assert "error" in result
    assert "999" in result["error"]


@pytest.mark.asyncio
async def test_mark_paid_preview_issues_hmac_token(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    preview = {"id": 1, "valor": 100.0, "dataEntrega": "2024-09-20"}
    client = _make_client(get_resp=_resp(200, preview))

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_paid", {"order_id": 1}, "http://x", "tok"
        )

    assert result["requires_confirmation"] is True
    assert result["confirm_token"]
    assert "." in result["confirm_token"]
    assert result["payload"] == {"order_id": 1}
    assert "Confirma" in result["message"]
    assert "R$" in result["message"]


@pytest.mark.asyncio
async def test_mark_paid_commit_requires_token_when_secret_set(monkeypatch):
    monkeypatch.setattr(settings, "confirm_token_secret", "test-secret-only-for-tests")
    client = _make_client(patch_resp=_resp(200, {"id": 1, "status": "PAGO"}))

    with patch("app.tools.registry.get_http_client", return_value=client):
        result = await execute_tool(
            "mark_order_as_paid",
            {"order_id": 1, "confirmed": True},
            "http://x",
            "tok",
        )

    assert "error" in result
    assert "confirmacao" in result["error"].lower()
