"""Testes do registry de tools e dos executores principais."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tools.registry import TOOL_DECLARATIONS, execute_tool
from app.tools.reports import (
    REPORT_TOOL_NAME,
    REPORT_ENDPOINT,
    REPORT_FILENAME,
    DECLARATIONS as REPORT_DECLARATIONS,
)


def test_all_declarations_have_unique_names():
    names = [d.name for d in TOOL_DECLARATIONS]
    assert len(names) == len(set(names)), "Duplicated tool names!"


def test_report_tool_registered():
    names = {d.name for d in TOOL_DECLARATIONS}
    assert REPORT_TOOL_NAME in names


def test_report_tool_has_no_required_params():
    decl = REPORT_DECLARATIONS[0]
    assert decl.parameters.type.name == "OBJECT"
    assert not decl.parameters.properties


def test_report_constants_consistent():
    assert REPORT_ENDPOINT.startswith("/")
    assert REPORT_FILENAME.endswith(".pdf")


@pytest.mark.asyncio
async def test_execute_unknown_tool_returns_error():
    result = await execute_tool("not_a_real_tool", {}, "http://x", None)
    assert "error" in result


@pytest.mark.asyncio
async def test_execute_report_tool_signals_ready():
    result = await execute_tool(REPORT_TOOL_NAME, {}, "http://x", None)
    assert result["ready"] is True
    assert result["filename"] == REPORT_FILENAME
    assert result["endpoint"] == REPORT_ENDPOINT
    assert "message" in result


@pytest.mark.asyncio
async def test_execute_dashboard_tool_calls_backend():
    fake_response = {"data": {"total": 42}}

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = lambda: fake_response
    fake_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("app.tools.registry.get_http_client", return_value=fake_client):
        result = await execute_tool(
            "get_unique_clients_count", {}, "http://localhost:8080", "token"
        )
    assert result == fake_response
    fake_client.get.assert_called_once()
    args, kwargs = fake_client.get.call_args
    assert "/dashboard/qtdClientesUnicos" in args[0]
    assert kwargs["headers"]["Authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_execute_dashboard_tool_no_token_no_auth_header():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json = lambda: {"data": []}
    fake_resp.raise_for_status = MagicMock()

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("app.tools.registry.get_http_client", return_value=fake_client):
        await execute_tool(
            "get_unique_clients_count", {}, "http://localhost:8080", None
        )

    _, kwargs = fake_client.get.call_args
    assert "Authorization" not in kwargs["headers"]


@pytest.mark.asyncio
async def test_execute_dashboard_tool_204_returns_empty():
    fake_resp = MagicMock()
    fake_resp.status_code = 204

    fake_client = MagicMock()
    fake_client.get = AsyncMock(return_value=fake_resp)

    with patch("app.tools.registry.get_http_client", return_value=fake_client):
        result = await execute_tool(
            "get_unique_clients_count", {}, "http://localhost:8080", None
        )
    assert result["data"] == []
    assert "message" in result


@pytest.mark.asyncio
async def test_execute_tool_handles_executor_exception():
    fake_client = MagicMock()
    fake_client.get = AsyncMock(side_effect=RuntimeError("network down"))

    with patch("app.tools.registry.get_http_client", return_value=fake_client):
        result = await execute_tool(
            "get_unique_clients_count", {}, "http://localhost:8080", None
        )
    assert "error" in result
    assert "network down" in result["error"]
