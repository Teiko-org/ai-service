from app.core.confirmation_ux import humanize_confirm_error, tool_result_for_llm


def test_humanize_strips_token_jargon():
    msg = humanize_confirm_error("Faltou o token de confirmacao.")
    assert "token" not in msg.lower()
    assert "sim" in msg.lower() or "confirmar" in msg.lower()


def test_humanize_keeps_bolo_catalog_lists():
    raw = (
        "Massa 'morango' nao encontrada no cadastro. "
        "Massas disponiveis: Baunilha, Chocolate."
    )
    assert humanize_confirm_error(raw) == raw


def test_tool_result_for_llm_passes_bolo_catalog_error():
    raw_err = (
        "Recheio 'ninho' nao encontrado no cadastro. "
        "Recheios unitarios disponiveis: Brigadeiro."
    )
    out = tool_result_for_llm({"error": raw_err})
    assert out["error"] == raw_err
    assert "get_fillings_catalog" in out.get("instruction", "").lower()


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
