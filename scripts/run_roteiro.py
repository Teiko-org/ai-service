#!/usr/bin/env python3
"""
Runner automatizado do ROTEIRO_KUROKO_ASSISTANTE.md via POST /api/v1/ask.

Nao usa UI do app — so HTTP no ai-service (mesmo fluxo do chat).

Uso (backend :8080 + uvicorn :8000 rodando):

  .\\.venv\\Scripts\\python.exe scripts\\run_roteiro.py --dry-run
  .\\.venv\\Scripts\\python.exe scripts\\run_roteiro.py --read-only
  .\\.venv\\Scripts\\python.exe scripts\\run_roteiro.py --with-writes --auto-confirm

Opcoes uteis:
  --config scripts/roteiro_config.json   IDs/datas reais
  --delay 20                             pausa entre passos (quota Gemini)
  --retry-wait 60                        espera antes de repetir falha
  --max-retries 2
  --from 7 --to 14                       intervalo de passos (numero do roteiro)
  --output reports/                      JSON + Markdown do run
  --resume                               mescla com checkpoint/ultimo parcial
  --merge-from reports/roteiro_run_X.json  mescla com relatorio anterior

Env: SMOKE_AI_URL, SMOKE_BEARER, GEMINI via .env do ai-service
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from roteiro_parser import RoteiroStep, apply_config, parse_roteiro_md  # noqa: E402
from roteiro_resolve import resolve_ids_from_assistant  # noqa: E402
ROTEIRO_MD = Path(__file__).resolve().parent / "ROTEIRO_KUROKO_ASSISTANTE.md"
CONFIG_EXAMPLE = Path(__file__).resolve().parent / "roteiro_config.example.json"

def _configure_stdout() -> None:
    """Evita crash no Windows (cp1252) ao imprimir emojis do WhatsApp etc."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc))


FAILURE_PHRASES = (
    "nao foi possivel gerar",
    "nao consegui processar",
    "nao consegui concluir",
    "alta demanda",
    "erro ao processar",
    "dados insuficientes",
    "nao esta cadastrad",
    "não está cadastrad",
    "por favor, escolha uma das opcoes",
    "por favor, escolha uma das opções",
    "confirmacao expirou",
)

_WRITE_SUCCESS_PHRASES = (
    "com sucesso",
    "criada com sucesso",
    "criado com sucesso",
    "encerrada com sucesso",
    "marcado como pago",
    "adicionados a fornada",
    "produtos adicionados",
)

_WRITE_PREVIEW_PHRASES = (
    "confirma?",
    "confirme",
    "vou criar",
    "vou adicionar",
    "vou encerrar",
    "vou substituir",
    "vou marcar",
    "vou cancelar",
)

_WRITE_TOOL_NAMES = frozenset(
    {
        "create_batch",
        "replace_active_batch",
        "close_batch",
        "add_batch_lines",
        "create_pedido_bolo",
        "create_pedido_bolo_full",
        "mark_order_as_paid",
        "cancel_order",
    }
)


def _merge_tools(*tool_lists: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for lst in tool_lists:
        for t in lst or []:
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def is_write_success(answer: str, tools_used: list[str]) -> bool:
    low = (answer or "").lower()
    if any(p in low for p in _WRITE_SUCCESS_PHRASES):
        return True
    tools = {str(t).lower() for t in tools_used}
    if "mark_order_as_paid" in tools:
        return "pago" in low
    if tools & {"create_pedido_bolo", "create_pedido_bolo_full"}:
        return "criado com sucesso" in low or "pedido #" in low
    if tools & {"create_batch", "replace_active_batch", "close_batch", "add_batch_lines"}:
        return any(
            x in low for x in ("sucesso", "encerrad", "criad", "adicionad", "substitu")
        )
    return False


def is_write_preview_only(answer: str, tools_used: list[str]) -> bool:
    if is_write_success(answer, tools_used):
        return False
    low = (answer or "").lower()
    return any(p in low for p in _WRITE_PREVIEW_PHRASES)


def tools_include_write(tools_used: list[str]) -> bool:
    """True se alguma tool de escrita/commit foi usada neste passo."""
    return bool({str(t) for t in tools_used} & _WRITE_TOOL_NAMES)


@dataclass
class StepResult:
    step_id: str
    title: str
    question: str
    ok: bool
    attempts: int
    answer: str = ""
    tools_used: list[str] = field(default_factory=list)
    pending_action: str | None = None
    issues: list[str] = field(default_factory=list)
    expected: str = ""
    skipped: bool = False
    skip_reason: str = ""


def _load_env() -> None:
    load_dotenv(ROOT / ".env")


def _load_config(path: Path | None, *, required: bool = False) -> dict:
    if path is None:
        default = ROOT / "scripts" / "roteiro_config.json"
        path = default if default.is_file() else None
    if path is None:
        if required:
            print("Informe --config ou crie scripts/roteiro_config.json", file=sys.stderr)
            sys.exit(1)
        return {}
    if not path.is_file():
        print(f"Config nao encontrado: {path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(path.read_text(encoding="utf-8"))


def _step_num(step_id: str) -> int:
    try:
        return int(step_id.split(".", 1)[0])
    except ValueError:
        return 0


def _step_sort_key(step_id: str) -> tuple[int, int]:
    parts = step_id.split(".", 1)
    try:
        return (int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
    except ValueError:
        return (9999, 0)


CHECKPOINT_NAME = "roteiro_checkpoint.json"


def _result_from_dict(raw: dict) -> StepResult:
    return StepResult(
        step_id=raw["step_id"],
        title=raw.get("title", ""),
        question=raw.get("question", ""),
        ok=bool(raw.get("ok")),
        attempts=int(raw.get("attempts") or 0),
        answer=raw.get("answer") or "",
        tools_used=list(raw.get("tools_used") or []),
        pending_action=raw.get("pending_action"),
        issues=list(raw.get("issues") or []),
        expected=raw.get("expected") or "",
        skipped=bool(raw.get("skipped")),
        skip_reason=raw.get("skip_reason") or "",
    )


def merge_step_results(
    prior: list[StepResult], new: list[StepResult]
) -> list[StepResult]:
    """Une por step_id; passos repetidos na retomada sobrescrevem o anterior."""
    by_id: dict[str, StepResult] = {r.step_id: r for r in prior}
    for r in new:
        by_id[r.step_id] = r
    return sorted(by_id.values(), key=lambda r: _step_sort_key(r.step_id))


def load_report_json(path: Path) -> tuple[list[StepResult], dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    results = [_result_from_dict(r) for r in data.get("results") or []]
    return results, dict(data.get("meta") or {})


def find_resume_source(output_dir: Path) -> Path | None:
    """Checkpoint em andamento ou ultimo relatorio parcial."""
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = output_dir / CHECKPOINT_NAME
    if checkpoint.is_file():
        try:
            meta = json.loads(checkpoint.read_text(encoding="utf-8")).get("meta") or {}
            if meta.get("status") in ("in_progress", "partial", "crashed"):
                return checkpoint
        except (json.JSONDecodeError, OSError):
            pass
    candidates: list[tuple[float, Path]] = []
    for p in output_dir.glob("roteiro_run_*.json"):
        if p.name == CHECKPOINT_NAME:
            continue
        try:
            meta = json.loads(p.read_text(encoding="utf-8")).get("meta") or {}
            if meta.get("status") in ("partial", "crashed", "in_progress"):
                candidates.append((p.stat().st_mtime, p))
        except (json.JSONDecodeError, OSError):
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _filter_steps(
    steps: list[RoteiroStep],
    *,
    from_num: int | None,
    to_num: int | None,
    read_only: bool,
    with_writes: bool,
    skip_manual: bool,
) -> list[RoteiroStep]:
    out: list[RoteiroStep] = []
    for s in steps:
        n = _step_num(s.step_id)
        if from_num is not None and n < from_num:
            continue
        if to_num is not None and n > to_num:
            continue
        if read_only and s.requires_writes:
            continue
        if not with_writes and s.requires_writes:
            continue
        if skip_manual and s.manual_only:
            continue
        out.append(s)
    return out


def _evaluate(step: RoteiroStep, data: dict) -> list[str]:
    issues: list[str] = []
    answer = (data.get("answer") or "").strip()
    low = answer.lower()

    if not answer:
        issues.append("resposta vazia")
    for phrase in FAILURE_PHRASES:
        if phrase in low:
            issues.append(f"resposta de falha: contem '{phrase}'")

    pending = data.get("pending_confirmation")
    exp = (step.expected or "").lower()

    if "previa" in exp or "confirma" in exp:
        if pending is None and "confirma" in step.question.lower():
            if not any(x in low for x in ("confirma", "substituir", "encerrar", "vou ")):
                issues.append("esperava previa ou pedido de confirmacao")

    if "whatsapp" in step.tags:
        if "segue o texto" in low and "message_text" not in str(data):
            issues.append("whatsapp: parece texto inventado (tem 'segue o texto')")
        if answer.startswith('"') and answer.endswith('"'):
            issues.append("whatsapp: resposta entre aspas (deveria ser texto puro)")

    if "pdf" in step.tags:
        tools = data.get("tools_used") or []
        if "generate_insights_report" not in tools:
            issues.append("pdf: tool generate_insights_report nao foi chamada")

    if "fornada ativa" in exp and "substituir" in exp:
        if "fornada" not in low:
            issues.append("bloqueio fornada: resposta nao menciona fornada")

    if any(x in low for x in ("nao encontrado", "não encontrado", "nao existe")):
        issues.append("recurso/id pode nao existir no ambiente — use --dynamic-ids")

    if "— —" in answer or "—  —" in answer:
        issues.append("formatacao: tracos duplos na listagem")

    if step.expected and not issues:
        if "pedido #" in exp and "pedido #" not in low and "#" not in answer:
            issues.append("nao citou numero de pedido")

    if step.requires_writes and not issues:
        tools = list(data.get("tools_used") or [])
        used_write = tools_include_write(tools)
        read_only_tools = bool(tools) and not used_write
        if read_only_tools:
            # Secao de escrita no roteiro pode ter passo so de leitura (ex. 34.2).
            pass
        elif is_write_preview_only(answer, tools):
            issues.append(
                "escrita: ficou na previa sem commit (sem tool de sucesso na resposta)"
            )
        elif not is_write_success(answer, tools):
            issues.append("escrita: nao houve confirmacao de sucesso no backend")

    return issues


class RoteiroRunner:
    def __init__(
        self,
        ai_url: str,
        bearer: str | None,
        *,
        delay_s: float,
        retry_wait_s: float,
        max_retries: int,
        auto_confirm: bool,
        timeout_s: float,
    ):
        self.ai_url = ai_url.rstrip("/")
        self.bearer = bearer
        self.delay_s = delay_s
        self.retry_wait_s = retry_wait_s
        self.max_retries = max_retries
        self.auto_confirm = auto_confirm
        self.timeout_s = timeout_s
        self.session_id: str | None = None
        self.client = httpx.Client(timeout=httpx.Timeout(timeout_s, connect=10.0))

    def _apply_auto_confirm(
        self, step: RoteiroStep, data: dict
    ) -> tuple[dict, list[str]]:
        """Confirma previas V2/V3: token na resposta ou 'Confirmo.' na sessao."""
        if not self.auto_confirm or not step.requires_writes:
            return data, list(data.get("tools_used") or [])

        tools_used = list(data.get("tools_used") or [])
        pending = data.get("pending_confirmation")
        if pending:
            conf = {
                "action": pending.get("action"),
                "confirm_token": pending.get("confirm_token"),
                "payload": pending.get("payload") or {},
            }
            if conf.get("action") and conf.get("confirm_token"):
                confirmed = self.ask("Confirmo.", confirmation=conf)
                tools_used = _merge_tools(tools_used, confirmed.get("tools_used"))
                return confirmed, tools_used

        answer = (data.get("answer") or "").strip()
        if is_write_preview_only(answer, tools_used) or pending:
            confirmed = self.ask("Confirmo.")
            tools_used = _merge_tools(tools_used, confirmed.get("tools_used"))
            if is_write_success(confirmed.get("answer") or "", tools_used):
                return confirmed, tools_used
            if not is_write_preview_only(confirmed.get("answer") or "", tools_used):
                return confirmed, tools_used

        return data, tools_used

    def close(self) -> None:
        self.client.close()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.bearer:
            h["Authorization"] = f"Bearer {self.bearer}"
        return h

    def ask(self, question: str, confirmation: dict | None = None) -> dict:
        body: dict = {"question": question, "session_id": self.session_id}
        if confirmation:
            body["confirmation"] = confirmation
        r = self.client.post(
            f"{self.ai_url}/api/v1/ask",
            json=body,
            headers=self._headers(),
        )
        r.raise_for_status()
        data = r.json()
        self.session_id = data.get("session_id") or self.session_id
        return data

    def run_step(self, step: RoteiroStep) -> StepResult:
        result = StepResult(
            step_id=step.step_id,
            title=step.title,
            question=step.question,
            ok=False,
            attempts=0,
            expected=step.expected,
        )

        if step.manual_only and not self.auto_confirm:
            result.skipped = True
            result.skip_reason = "manual_only (use --include-manual ou faca no app)"
            result.ok = True
            return result

        last_data: dict = {}
        for attempt in range(1, self.max_retries + 2):
            result.attempts = attempt
            try:
                data = self.ask(step.question)
                last_data = data
                initial_pending = data.get("pending_confirmation")

                data, tools_used = self._apply_auto_confirm(step, data)
                last_data = data

                issues = _evaluate(step, data)
                result.answer = (data.get("answer") or "").strip()
                result.tools_used = tools_used
                if initial_pending and not self.auto_confirm:
                    result.pending_action = initial_pending.get("action")

                if not issues:
                    result.ok = True
                    result.issues = []
                    return result

                result.issues = issues
            except httpx.HTTPError as exc:
                result.issues = [f"HTTP: {exc}"]
            except Exception as exc:  # noqa: BLE001
                result.issues = [f"erro: {exc}"]

            if attempt <= self.max_retries:
                print(
                    f"    retry em {int(self.retry_wait_s)}s "
                    f"({'; '.join(result.issues)})"
                )
                time.sleep(self.retry_wait_s)

        if last_data:
            result.answer = (last_data.get("answer") or result.answer).strip()
            result.tools_used = list(last_data.get("tools_used") or [])
        return result


def _summarize_results(results: list[StepResult]) -> dict[str, int]:
    return {
        "total": len(results),
        "ok": sum(1 for r in results if r.ok and not r.skipped),
        "fail": sum(1 for r in results if not r.ok and not r.skipped),
        "skip": sum(1 for r in results if r.skipped),
    }


def save_checkpoint(
    output_dir: Path,
    results: list[StepResult],
    meta: dict,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / CHECKPOINT_NAME
    payload = {"meta": meta, "results": [asdict(r) for r in results]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_report(
    output_dir: Path,
    results: list[StepResult],
    meta: dict,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = output_dir / f"roteiro_run_{stamp}.json"
    md_path = output_dir / f"roteiro_run_{stamp}.md"

    payload = {"meta": meta, "results": [asdict(r) for r in results]}
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary = _summarize_results(results)
    status = meta.get("status", "complete")
    lines = [
        f"# Roteiro Kuroko — {meta.get('started_at', '')}",
        "",
        f"- Status: **{status}**",
        f"- Passos no relatorio: {summary['total']} | OK: {summary['ok']} | "
        f"Falha: {summary['fail']} | Pulados: {summary['skip']}",
        f"- AI: `{meta.get('ai_url')}` | session: `{meta.get('session_id')}`",
    ]
    if meta.get("last_step_id"):
        lines.append(f"- Ultimo passo registrado: **{meta['last_step_id']}**")
    if meta.get("crash_error"):
        lines.append(f"- Erro do runner: `{meta['crash_error']}`")
    merged_from = meta.get("merged_from") or []
    if merged_from:
        lines.append(f"- Mesclado com: {', '.join(merged_from)}")
    run_parts = meta.get("run_parts") or []
    if len(run_parts) > 1:
        lines.append(f"- Execucoes: {len(run_parts)} partes (`run_parts` no JSON)")
    lines.append("")
    for r in results:
        icon = "SKIP" if r.skipped else ("OK" if r.ok else "FAIL")
        lines.append(f"## [{icon}] {r.step_id} — {r.title}")
        lines.append("")
        lines.append(f"**Pergunta:** {r.question}")
        lines.append("")
        if r.expected:
            lines.append(f"**Esperado (roteiro):** {r.expected}")
            lines.append("")
        if r.skipped:
            lines.append(f"_{r.skip_reason}_")
        else:
            lines.append(f"**Tools:** {', '.join(r.tools_used) or '—'}")
            lines.append("")
            lines.append("**Resposta:**")
            lines.append("")
            lines.append("```")
            lines.append(r.answer or "(vazia)")
            lines.append("```")
            if r.issues:
                lines.append("")
                lines.append(f"**Problemas:** {'; '.join(r.issues)}")
        lines.append("")
        lines.append("---")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main() -> int:
    _configure_stdout()
    _load_env()
    import os

    p = argparse.ArgumentParser(description="Executa roteiro Kuroko via /ask")
    p.add_argument("--ai-url", default=os.environ.get("SMOKE_AI_URL", "http://localhost:8000"))
    p.add_argument("--config", type=Path, default=None, help="roteiro_config.json")
    p.add_argument("--output", type=Path, default=ROOT / "reports")
    p.add_argument("--dry-run", action="store_true", help="So lista passos")
    p.add_argument("--read-only", action="store_true", help="Sem escrita V3/acoes")
    p.add_argument("--with-writes", action="store_true", help="Inclui passos de escrita")
    p.add_argument("--auto-confirm", action="store_true", help="Confirma previas automaticamente")
    p.add_argument("--include-manual", action="store_true", help="Inclui passos manuais")
    p.add_argument("--from", dest="from_num", type=int, default=None)
    p.add_argument("--to", dest="to_num", type=int, default=None)
    p.add_argument("--delay", type=float, default=18.0, help="Segundos entre passos")
    p.add_argument("--retry-wait", type=float, default=60.0)
    p.add_argument("--max-retries", type=int, default=2)
    p.add_argument("--timeout", type=float, default=120.0)
    p.add_argument("--health-only", action="store_true")
    p.add_argument(
        "--dynamic-ids",
        action="store_true",
        default=True,
        help="Lista pedidos/massas/fornada no sistema e adapta IDs do roteiro (padrao).",
    )
    p.add_argument(
        "--static-ids",
        action="store_true",
        help="Usa so roteiro_config.json; nao faz listagens previas.",
    )
    p.add_argument(
        "--merge-from",
        type=Path,
        default=None,
        help="JSON/checkpoint anterior para unir resultados (mesmo step_id = esta run vence).",
    )
    p.add_argument(
        "--resume",
        action="store_true",
        help="Mescla com roteiro_checkpoint.json ou ultimo relatorio parcial em --output.",
    )
    args = p.parse_args()
    dynamic_ids = args.dynamic_ids and not args.static_ids

    bearer = (os.environ.get("SMOKE_BEARER") or "").strip() or None
    file_config = _load_config(args.config, required=args.static_ids)

    def _prepare_steps(merged_config: dict) -> list[RoteiroStep]:
        prepared = apply_config(parse_roteiro_md(ROTEIRO_MD), merged_config)
        return _filter_steps(
            prepared,
            from_num=args.from_num,
            to_num=args.to_num,
            read_only=args.read_only,
            with_writes=args.with_writes,
            skip_manual=not args.include_manual,
        )

    print(f"Roteiro: {ROTEIRO_MD.name}")
    print(f"AI: {args.ai_url} | auto_confirm={args.auto_confirm}")
    print(f"IDs: {'dinamicos (listar antes)' if dynamic_ids else 'estaticos (config)'}")

    if args.dry_run:
        steps = _prepare_steps(file_config)
        print(f"Passos selecionados: {len(steps)}")
        if dynamic_ids:
            print(
                "  (com --dynamic-ids, na execucao real o runner pergunta "
                "pedidos recentes / massas / fornada ativa e substitui 42, 15, etc.)"
            )
        for s in steps:
            flags = []
            if s.requires_writes:
                flags.append("write")
            if s.manual_only:
                flags.append("manual")
            print(f"  [{s.step_id}] ({','.join(flags) or 'read'}) {s.question[:70]}...")
        return 0

    runner = RoteiroRunner(
        args.ai_url,
        bearer,
        delay_s=args.delay,
        retry_wait_s=args.retry_wait,
        max_retries=args.max_retries,
        auto_confirm=args.auto_confirm,
        timeout_s=args.timeout,
    )

    models_meta: dict = {}
    try:
        h = runner.client.get(f"{args.ai_url}/api/v1/health")
        h.raise_for_status()
        print("Health OK")
        try:
            ms = runner.client.get(f"{args.ai_url}/api/v1/models-status")
            if ms.status_code == 200:
                models_meta = ms.json()
                chain = models_meta.get("model_chain") or []
                if chain:
                    print(f"Cadeia de modelos: {' -> '.join(chain)}")
        except Exception:  # noqa: BLE001
            models_meta = {}
    except Exception as exc:  # noqa: BLE001
        print(f"AI service indisponivel: {exc}", file=sys.stderr)
        return 1

    if args.health_only:
        runner.close()
        return 0

    merged_config = dict(file_config)
    probe_log: list = []
    if dynamic_ids:
        print()
        print("=== Resolvendo IDs reais (listagens, sem alterar banco) ===")
        runtime, probe_log = resolve_ids_from_assistant(
            runner.ask,
            delay_s=max(args.delay, 12.0),
            on_probe=lambda p: print(
                f"  [{p.key}] {'OK' if p.ok else 'FALHA'} — {p.question[:50]}..."
            ),
        )
        merged_config.update(runtime)
        runner.session_id = None
        print()
        if merged_config.get("order_id"):
            print("IDs para o roteiro:")
            for key in sorted(merged_config):
                if key.startswith("_"):
                    continue
                print(f"  {key}: {merged_config[key]}")
        else:
            print(
                "AVISO: nenhum pedido extraido das listagens. "
                "Confira backend + Gemini; passos com ID tendem a falhar."
            )
    elif not file_config:
        print(
            "AVISO: sem --dynamic-ids e sem roteiro_config.json — "
            "o roteiro usa IDs de exemplo (42, 15...) que podem nao existir."
        )

    steps = _prepare_steps(merged_config)
    print(f"Passos selecionados: {len(steps)}")

    prior_results: list[StepResult] = []
    prior_meta: dict = {}
    merged_from: list[str] = []
    merge_path: Path | None = args.merge_from
    if args.resume and merge_path is None:
        merge_path = find_resume_source(args.output)
    if merge_path and merge_path.is_file():
        prior_results, prior_meta = load_report_json(merge_path)
        merged_from.append(str(merge_path.name))
        print(f"Mesclando com relatorio anterior: {merge_path.name} ({len(prior_results)} passos)")

    started = datetime.now(timezone.utc).isoformat()
    run_parts: list[dict] = list(prior_meta.get("run_parts") or [])
    run_parts.append(
        {
            "started_at": started,
            "from_step": args.from_num,
            "to_step": args.to_num,
            "steps_in_run": len(steps),
        }
    )

    results: list[StepResult] = []
    combined: list[StepResult] = merge_step_results(prior_results, [])
    crash_error: str | None = None
    last_step_id: str | None = None

    base_meta = {
        "started_at": prior_meta.get("started_at") or started,
        "ai_url": args.ai_url,
        "read_only": args.read_only,
        "with_writes": args.with_writes,
        "auto_confirm": args.auto_confirm,
        "dynamic_ids": dynamic_ids,
        "resolved_config": merged_config,
        "probes": [asdict(p) for p in probe_log] if probe_log else prior_meta.get("probes", []),
        "models_status": models_meta or prior_meta.get("models_status", {}),
        "merged_from": merged_from,
        "run_parts": run_parts,
        "status": "in_progress",
    }

    def _persist_progress(*, crashed: str | None = None) -> tuple[list[StepResult], dict]:
        merged = merge_step_results(prior_results, results)
        if crashed:
            status = "crashed"
        elif len(results) < len(steps):
            status = "partial"
        elif any(not r.ok and not r.skipped for r in results):
            status = "partial"
        else:
            status = "complete"
        meta = {
            **base_meta,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "session_id": runner.session_id,
            "last_step_id": last_step_id,
            "crash_error": crashed,
            "status": status,
            "steps_this_run": len(results),
            "steps_planned_this_run": len(steps),
        }
        save_checkpoint(args.output, merged, meta)
        return merged, meta

    try:
        for i, step in enumerate(steps, start=1):
            print()
            print(f"[{i}/{len(steps)}] {step.step_id} {step.title}")
            print(f"  Q: {step.question}")
            try:
                sr = runner.run_step(step)
            except Exception as exc:  # noqa: BLE001
                sr = StepResult(
                    step_id=step.step_id,
                    title=step.title,
                    question=step.question,
                    ok=False,
                    attempts=1,
                    issues=[f"crash no runner: {exc}"],
                )
                results.append(sr)
                last_step_id = step.step_id
                crash_error = str(exc)
                raise

            results.append(sr)
            last_step_id = step.step_id
            status = "OK" if sr.ok else ("SKIP" if sr.skipped else "FAIL")
            print(f"  => {status}")
            if sr.answer and not sr.skipped:
                preview = sr.answer.replace("\n", " ")[:160]
                _safe_print(f"  A: {preview}...")
            if sr.issues:
                _safe_print(f"  ! {sr.issues}")

            combined, _prog_meta = _persist_progress()
            if i < len(steps) and args.delay > 0:
                time.sleep(args.delay)

    except (KeyboardInterrupt, SystemExit) as exc:
        crash_error = str(exc) if str(exc) else type(exc).__name__
        raise
    except Exception as exc:  # noqa: BLE001
        if crash_error is None:
            crash_error = str(exc)
    finally:
        combined, final_meta = _persist_progress(crashed=crash_error)
        final_meta["finished_at"] = datetime.now(timezone.utc).isoformat()
        runner.close()
        json_path, md_path = _write_report(args.output, combined, final_meta)
        if final_meta["status"] == "complete":
            checkpoint = args.output / CHECKPOINT_NAME
            if checkpoint.is_file():
                try:
                    checkpoint.unlink()
                except OSError:
                    pass
        print()
        print(f"Relatorio JSON: {json_path}")
        print(f"Relatorio MD:   {md_path}")
        print(f"Checkpoint:     {args.output / CHECKPOINT_NAME}")
        if crash_error:
            print(f"Run interrompido: {crash_error}", file=sys.stderr)

    failed = [r for r in combined if not r.ok and not r.skipped]
    if crash_error:
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
