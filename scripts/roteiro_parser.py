"""Extrai passos executaveis de ROTEIRO_KUROKO_ASSISTANTE.md."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

STEP_HEAD = re.compile(r"^(\d+)\.\s+\*\*(.+?)\*\*")
SECTION_HEAD = re.compile(r"^##\s+(\d+)\.\s+(.+)$")
QUESTION_BULLET = re.compile(r"^\s*-\s+`([^`]+)`\s*$")
TABLE_MSG = re.compile(r"^\|\s*[A-Z]\s*\|\s*`([^`]+)`\s*\|")
EXPECTED_LINE = re.compile(r"\*\*Esperado:\*\*\s*(.+)$", re.IGNORECASE)
TOOL_LINE = re.compile(r"\*\*Tool:\*\*\s*(.+)$", re.IGNORECASE)

WRITE_SECTION_MARKERS = (
    "escrita v3",
    "acoes em pedidos",
    "fluxo completo",
)


@dataclass
class RoteiroStep:
    step_id: str
    section: str
    title: str
    question: str
    expected: str = ""
    notes: str = ""
    requires_writes: bool = False
    needs_confirm: bool = False
    manual_only: bool = False
    tags: list[str] = field(default_factory=list)


def _section_flags(section_title: str) -> tuple[bool, bool]:
    low = section_title.lower()
    requires_writes = any(m in low for m in WRITE_SECTION_MARKERS)
    needs_confirm = requires_writes or "acoes em pedidos" in low
    return requires_writes, needs_confirm


def parse_roteiro_md(path: Path) -> list[RoteiroStep]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_section = "intro"
    section_requires_writes = False
    section_needs_confirm = False

    current_num: str | None = None
    current_title = ""
    expected_buf: list[str] = []
    notes_buf: list[str] = []
    sub_index = 0

    steps: list[RoteiroStep] = []

    def flush_question(question: str) -> None:
        nonlocal sub_index
        if not question.strip():
            return
        sid = f"{current_num}.{sub_index}" if current_num else f"x.{sub_index}"
        sub_index += 1
        manual = any(
            x in question.lower()
            for x in ("nao, cancela", "botao **cancelar**", "confirme botao")
        ) or "recusar confirmacao" in current_title.lower()
        tags: list[str] = []
        if "whatsapp" in current_title.lower() or "whatsapp" in question.lower():
            tags.append("whatsapp")
        if "pdf" in current_title.lower() or "relatorio" in question.lower():
            tags.append("pdf")
        if "pendente" in question.lower():
            tags.append("filter")

        steps.append(
            RoteiroStep(
                step_id=sid,
                section=current_section,
                title=current_title or question[:40],
                question=question.strip(),
                expected=" ".join(expected_buf).strip(),
                notes=" ".join(notes_buf).strip(),
                requires_writes=section_requires_writes
                or "cria " in question.lower()
                or "encerra" in question.lower()
                or "substitui" in question.lower()
                or "adiciona" in question.lower()
                or "marca o pedido" in question.lower()
                or "cancela o pedido" in question.lower(),
                needs_confirm=section_needs_confirm
                or "confirma" in question.lower()
                or "marca o pedido" in question.lower(),
                manual_only=manual,
                tags=tags,
            )
        )

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            continue
        if line.startswith("|") and "---" in line:
            continue

        sec = SECTION_HEAD.match(line)
        if sec:
            current_section = sec.group(2).strip()
            section_requires_writes, section_needs_confirm = _section_flags(
                current_section
            )
            continue

        m_step = STEP_HEAD.match(line)
        if m_step:
            current_num = m_step.group(1)
            current_title = m_step.group(2).strip()
            expected_buf = []
            notes_buf = []
            sub_index = 0
            continue

        tbl = TABLE_MSG.match(line)
        if tbl:
            current_num = current_num or "demo"
            current_title = current_title or "Fluxo demo"
            flush_question(tbl.group(1))
            continue

        q = QUESTION_BULLET.match(line)
        if q:
            flush_question(q.group(1))
            continue

        exp = EXPECTED_LINE.search(line)
        if exp:
            expected_buf.append(exp.group(1).strip())
            continue

        tool = TOOL_LINE.search(line)
        if tool:
            notes_buf.append(f"Tool: {tool.group(1).strip()}")

    return steps


def apply_config(steps: list[RoteiroStep], config: dict) -> list[RoteiroStep]:
    """Substitui IDs/datas de exemplo do roteiro pelos valores em config."""
    if not config:
        return steps

    def _cfg(key: str, fallback: int | str) -> str:
        val = config.get(key, fallback)
        return str(val)

    delivery_iso = _cfg(
        "delivery_date_iso",
        _cfg("delivery_date", "2026-05-15"),
    )
    replacements = [
        # Datas completas antes de substituir numeros soltos (evita 2026-05-15 -> 2026-05-3020)
        (r"2026-05-15", delivery_iso),
        (
            r"10/06/2026 a 16/06/2026",
            f"{_cfg('fornada_inicio', '10/06/2026')} a {_cfg('fornada_fim', '16/06/2026')}",
        ),
        (
            r"20/06/2026 a 26/06/2026",
            f"{_cfg('fornada_substituir_inicio', '20/06/2026')} a "
            f"{_cfg('fornada_substituir_fim', '26/06/2026')}",
        ),
        (r"janeiro de 2026", _cfg("batch_month_label", "janeiro de 2026")),
        # IDs de pedido — sempre com contexto (nao usar \b15\b etc.)
        (r"pedido de bolo 15\b", f"pedido de bolo {_cfg('order_id_bolo', _cfg('order_id', 15))}"),
        (r"pedido de fornada 3\b", f"pedido de fornada {_cfg('order_id_fornada', 3)}"),
        (r"pedido 42\b", f"pedido {_cfg('order_id_pay', _cfg('order_id', 42))}"),
        (r"pedido 38\b", f"pedido {_cfg('order_id_cancel', 38)}"),
        (r"pedido 15\b", f"pedido {_cfg('order_id_bolo', _cfg('order_id', 15))}"),
        (r"resumo do pedido 42\b", f"resumo do pedido {_cfg('order_id', 42)}"),
        (r"\b42\b", _cfg("order_id_pay", _cfg("order_id", 42))),
        (r"\b38\b", _cfg("order_id_cancel", 38)),
        (r"\b99\b", _cfg("order_id_fake", 99)),
        (r"#10 e #11", f"#{_cfg('order_whatsapp_1', 10)} e #{_cfg('order_whatsapp_2', 11)}"),
        (
            r"pedidos #10 e #11",
            f"pedidos #{_cfg('order_whatsapp_1', 10)} e #{_cfg('order_whatsapp_2', 11)}",
        ),
        (r"\bid 2\b", f"id {_cfg('massa_id', 2)}"),
        (r"fornada 14\b", f"fornada {_cfg('batch_id', 14)}"),
        (
            r"fornadas 10 e 11",
            f"fornadas {_cfg('batch_close_1', 10)} e {_cfg('batch_close_2', 11)}",
        ),
        (r"massa Cacau\b", f"massa {_cfg('massa_nome', 'Cacau')}"),
        (
            r"retirada dia 10/06/2026",
            f"retirada dia {_cfg('pedido_retirada_data', '10/06/2026')}",
        ),
    ]
    out: list[RoteiroStep] = []
    for step in steps:
        q = step.question
        for pattern, repl in replacements:
            q = re.sub(pattern, repl, q)
        out.append(
            RoteiroStep(
                step_id=step.step_id,
                section=step.section,
                title=step.title,
                question=q,
                expected=step.expected,
                notes=step.notes,
                requires_writes=step.requires_writes,
                needs_confirm=step.needs_confirm,
                manual_only=step.manual_only,
                tags=list(step.tags),
            )
        )
    return out
