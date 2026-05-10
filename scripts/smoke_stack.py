#!/usr/bin/env python3
"""
Smoke test da integracao: Backend Java <-> AI Service (e opcionalmente /ask com Gemini).

Uso (na pasta ai-service, com backend :8080 e uvicorn :8000 rodando):

  .\\.venv\\Scripts\\python.exe scripts\\smoke_stack.py

Opcional — testa tambem POST /ask (consome quota Gemini):

  .\\.venv\\Scripts\\python.exe scripts\\smoke_stack.py --with-ask

Variaveis de ambiente (opcionais; carrega .env na raiz do ai-service):

  CARAMBOLOS_API_URL   default http://localhost:8080
  SMOKE_AI_URL         default http://localhost:8000
  SMOKE_BEARER         JWT opcional (mesmo token do app, se o AI repassar ao Java)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv


@dataclass
class SmokeResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    results: list[SmokeResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append(SmokeResult(name=name, ok=ok, detail=detail))

    def print_summary(self) -> int:
        ok_n = sum(1 for r in self.results if r.ok)
        total = len(self.results)
        print()
        print("=" * 60)
        for r in self.results:
            status = "OK   " if r.ok else "FALHA"
            line = f"[{status}] {r.name}"
            if r.detail and not r.ok:
                line += f" - {r.detail}"
            print(line)
        print("=" * 60)
        print(f"Resumo: {ok_n}/{total} passaram")
        return 0 if ok_n == total else 1


def _load_env() -> None:
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")


def _headers_bearer() -> dict[str, str]:
    token = (os.environ.get("SMOKE_BEARER") or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def run_smoke(backend: str, ai: str, with_ask: bool) -> int:
    rep = Report()
    h = _headers_bearer()
    timeout = httpx.Timeout(30.0, connect=5.0)

    with httpx.Client(timeout=timeout) as client:
        # --- Backend ---
        try:
            r = client.get(f"{backend}/dashboard/qtdPedidos", headers=h)
            r.raise_for_status()
            body = r.json()
            if not isinstance(body, dict):
                rep.add("Backend GET /dashboard/qtdPedidos", False, "JSON nao e objeto")
            else:
                rep.add("Backend GET /dashboard/qtdPedidos", True)
        except Exception as exc:  # noqa: BLE001
            rep.add("Backend GET /dashboard/qtdPedidos", False, str(exc))

        try:
            r = client.get(f"{backend}/fornadas/proxima", headers=h)
            if r.status_code not in (200, 204):
                rep.add("Backend GET /fornadas/proxima", False, f"HTTP {r.status_code}")
            else:
                rep.add("Backend GET /fornadas/proxima", True)
        except Exception as exc:  # noqa: BLE001
            rep.add("Backend GET /fornadas/proxima", False, str(exc))

        try:
            r = client.get(f"{backend}/resumo-pedido", params={"page": 0, "size": 3}, headers=h)
            if r.status_code not in (200, 204):
                rep.add("Backend GET /resumo-pedido", False, f"HTTP {r.status_code}")
            else:
                rep.add("Backend GET /resumo-pedido", True)
        except Exception as exc:  # noqa: BLE001
            rep.add("Backend GET /resumo-pedido", False, str(exc))

        # --- AI Service (sem depender do Java para health) ---
        try:
            r = client.get(f"{ai}/api/v1/health")
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "ok":
                rep.add("AI GET /api/v1/health", False, json.dumps(data)[:200])
            else:
                rep.add("AI GET /api/v1/health", True)
        except Exception as exc:  # noqa: BLE001
            rep.add("AI GET /api/v1/health", False, str(exc))

        try:
            r = client.get(f"{ai}/api/v1/suggested-prompts")
            r.raise_for_status()
            data = r.json()
            n = len(data.get("prompts", []))
            rep.add(f"AI GET /api/v1/suggested-prompts ({n} pills)", True)
        except Exception as exc:  # noqa: BLE001
            rep.add("AI GET /api/v1/suggested-prompts", False, str(exc))

        # Alertas: o executor chama o backend (integracao AI -> Java)
        try:
            r = client.get(f"{ai}/api/v1/alerts", params={"refresh": "true"}, headers=h)
            r.raise_for_status()
            data = r.json()
            n_alerts = len(data.get("alerts", []))
            rep.add(
                f"AI GET /api/v1/alerts?refresh=true (gerou {n_alerts} alerta(s))",
                True,
            )
        except Exception as exc:  # noqa: BLE001
            rep.add("AI GET /api/v1/alerts (AI -> backend real)", False, str(exc))

        if with_ask:
            try:
                r = client.post(
                    f"{ai}/api/v1/ask",
                    json={
                        "question": (
                            "Responda em uma frase: quantos pedidos temos por status?"
                        ),
                        "session_id": None,
                    },
                    headers={**h, "Content-Type": "application/json"},
                )
                r.raise_for_status()
                data = r.json()
                tools = data.get("tools_used") or []
                rep.add(
                    f"AI POST /api/v1/ask (tools: {', '.join(tools) or 'nenhuma'})",
                    bool(data.get("answer")),
                    "" if data.get("answer") else "resposta vazia",
                )
            except Exception as exc:  # noqa: BLE001
                rep.add("AI POST /api/v1/ask (Gemini + tools -> backend)", False, str(exc))

    return rep.print_summary()


def main() -> None:
    _load_env()
    p = argparse.ArgumentParser(description="Smoke: Backend + AI Service (+ opcional /ask)")
    p.add_argument(
        "--backend-url",
        default=os.environ.get("CARAMBOLOS_API_URL", "http://localhost:8080").rstrip("/"),
    )
    p.add_argument(
        "--ai-url",
        default=os.environ.get("SMOKE_AI_URL", "http://localhost:8000").rstrip("/"),
    )
    p.add_argument(
        "--with-ask",
        action="store_true",
        help="Inclui POST /ask (usa GEMINI_API_KEY do .env; consome quota).",
    )
    args = p.parse_args()

    print("Smoke stack")
    print(f"  Backend: {args.backend_url}")
    print(f"  AI:      {args.ai_url}")
    print(f"  Bearer:  {'sim (SMOKE_BEARER)' if _headers_bearer() else 'nao'}")
    print(f"  /ask:    {'sim (--with-ask)' if args.with_ask else 'nao'}")

    code = run_smoke(args.backend_url, args.ai_url, args.with_ask)
    sys.exit(code)


if __name__ == "__main__":
    main()
