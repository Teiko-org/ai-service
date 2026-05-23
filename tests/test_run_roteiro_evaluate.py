"""Heuristicas de sucesso/previa do runner do roteiro."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from roteiro_parser import RoteiroStep
from run_roteiro import _evaluate, is_write_preview_only, is_write_success, tools_include_write


def test_write_success_bolo():
    assert is_write_success(
        "Pedido #3022 criado com sucesso.",
        ["create_pedido_bolo_full"],
    )


def test_write_preview_only_create_batch():
    assert is_write_preview_only(
        "Vou criar uma nova fornada de 22/06 a 28/06. Confirma?",
        [],
    )


def test_evaluate_fails_preview_without_commit():
    step = RoteiroStep(
        step_id="23.0",
        section="x",
        title="Criar",
        question="Cria uma fornada",
        requires_writes=True,
    )
    issues = _evaluate(
        step,
        {
            "answer": "Vou criar uma fornada. Confirma?",
            "tools_used": [],
        },
    )
    assert any("previa sem commit" in i for i in issues)


def test_evaluate_ok_after_success():
    step = RoteiroStep(
        step_id="28.0",
        section="x",
        title="Add",
        question="Adiciona produtos",
        requires_writes=True,
    )
    issues = _evaluate(
        step,
        {
            "answer": "Produtos adicionados a fornada #21 com sucesso.",
            "tools_used": ["add_batch_lines"],
        },
    )
    assert issues == []


def test_tools_include_write():
    assert tools_include_write(["add_batch_lines"])
    assert not tools_include_write(["get_active_batch_with_products"])


def test_evaluate_read_step_in_write_section_ok():
    """Passo 34.2: secao demo/escrita mas tool so de leitura — nao exige 'com sucesso'."""
    step = RoteiroStep(
        step_id="34.2",
        section="Fluxo completo sugerido",
        title="Mostra fornada",
        question="Mostra a fornada ativa e os produtos",
        requires_writes=True,
    )
    issues = _evaluate(
        step,
        {
            "answer": (
                "A fornada ativa e a #26. Produtos: Pao Frances x5, Croissant x3."
            ),
            "tools_used": ["get_active_batch_with_products"],
        },
    )
    assert issues == []
