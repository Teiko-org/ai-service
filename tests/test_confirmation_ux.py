from app.core.confirmation_ux import humanize_confirm_error, tool_result_for_llm


def test_humanize_strips_token_jargon():
    msg = humanize_confirm_error("Faltou o token de confirmacao.")
    assert "token" not in msg.lower()
    assert "sim" in msg.lower() or "confirmar" in msg.lower()


def test_tool_result_for_llm_hides_confirm_token():
    raw = {
        "requires_confirmation": True,
        "confirm_token": "123.abc",
        "message": "Vou marcar o pedido #1 como PAGO. Confirma?",
        "payload": {"order_id": 1},
    }
    out = tool_result_for_llm(raw)
    assert "confirm_token" not in out
    assert out["message"] == raw["message"]
    assert "confirmed=True" in out.get("instruction", "")
