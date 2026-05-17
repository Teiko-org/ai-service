# AI Service — Assistente Inteligente Carambolos

Microservico Python/FastAPI que funciona como Assistente Inteligente de Negocio para a confeitaria Carambolos. Consome a API REST do backend Java (Spring Boot) e usa Google Gemini com Function Calling para responder perguntas em linguagem natural e gerar insights proativos.

## Arquitetura

```
Mobile/Web → FastAPI (este servico) → Gemini API (Function Calling)
                                          ↓
                                    Backend Java (Spring Boot :8080)
                                          ↓
                                       MySQL 8
```

Este servico NAO acessa o banco diretamente. Toda leitura de dados passa pelas tools que chamam endpoints REST do backend Java.

## Estrutura

- `app/api/` — Rotas HTTP e seguranca (deps.py). Sem AGENTS.md proprio, coberto por este node.
- `app/core/` — Logica do assistente, client Gemini, prompts, sessoes, cache, gerenciador de modelos, rate limiter, http client compartilhado. Ver `app/core/AGENTS.md`.
- `app/tools/` — Function Declarations do Gemini mapeadas a endpoints do backend. Inclui `order_ref.py` (normaliza numero do pedido #X / pedido X para id do resumo). Ver `app/tools/AGENTS.md`.
- `app/models/` — Schemas Pydantic (request/response). Arquivo unico, coberto por este node.
- `tests/` — Testes com pytest. Mocks para Gemini, TestClient do FastAPI.

## Endpoints

| Metodo | Rota                       | Rate Limit  | Auth    | Descricao                                          |
|--------|----------------------------|-------------|---------|----------------------------------------------------|
| GET    | /api/v1/health             | 60/min      | Nao     | Health check                                       |
| POST   | /api/v1/ask                | 15/min      | Bearer  | Pergunta em linguagem natural                      |
| POST   | /api/v1/insights           | 10/min      | Bearer  | Insights proativos (com cache 5min)                |
| GET    | /api/v1/alerts             | 30/min      | Bearer  | Alertas proativos (cache 30min, refresh em bg)     |
| GET    | /api/v1/suggested-prompts  | 60/min      | Nao     | Prompts pre-definidos (pills)                      |
| GET    | /api/v1/models-status      | 60/min      | Nao     | Status/cooldown dos modelos Gemini                 |

## Configuracao

Tudo via `.env` / variavel de ambiente. Validado por `pydantic-settings` em `app/config.py`:
- `GEMINI_API_KEY` (obrigatorio) — Chave do Google AI Studio
- `GEMINI_MODEL` — Modelo Gemini principal (default: `gemini-2.5-flash-lite`)
- `CARAMBOLOS_API_URL` — URL do backend Java (default: `http://localhost:8080`)
- `ALLOWED_ORIGINS` — CORS origins separados por virgula
- `LOG_LEVEL` — DEBUG, INFO, WARNING, ERROR

Modelos de fallback definidos em `FALLBACK_MODELS` no `config.py`:
- `gemini-2.5-flash-lite` (principal — 15 RPM, 1.000 RPD)
- `gemini-2.5-flash` (fallback — 10 RPM, 250 RPD)
- `gemini-2.5-pro` (ultimo recurso — 5 RPM, 100 RPD)

## Seguranca (Defense in Depth)

1. **CORS** — Origins restritas via config
2. **Rate limiting** — slowapi, limites por endpoint (mais restrito em /ask e /insights)
3. **Sanitizacao** — Input truncado a 1000 chars, strip
4. **Prompt guard** — Regex em `deps.py` bloqueia padroes de injection (PT e EN)
5. **Content guardrails** — Filtros de profanidade e conteudo off-topic em `deps.py`, retorna mensagem amigavel (HTTP 400)
6. **System prompt** — Instrucoes anti-jailbreak e politica de conteudo embutidas no prompt do Gemini
7. **Auth** — Bearer token repassado ao backend Java (validacao JWT e feita la)

## Fluxo principal (/ask)

1. Request chega → sanitize → check injection → check content policy → extract token
2. SessionStore busca/cria sessao e carrega historico (ultimas 10 mensagens)
3. `CarambolosAssistant.ask()` envia pergunta + historico + TOOL_DECLARATIONS ao Gemini
4. `_generate_with_fallback()` tenta o modelo principal; se 429/404, `mark_rate_limited` escolhe outro modelo (ou libera o de cooldown mais curto se todos estiverem bloqueados)
5. Se resposta contem `function_call(s)` → registry despacha para executor correto
6. Executor chama endpoint do backend Java via httpx compartilhado (em `actions` e `deep_orders`, `order_id` e normalizado — ex.: `#42` e o mesmo resumo que `42`)
7. Resultado volta pro Gemini como function_response
8. Loop ate max 5 rounds ou Gemini responder com texto final
9. Mensagens (user + assistant) sao salvas na sessao
10. Response com `answer`, `tools_used` e `session_id`

## Fluxo de insights (/insights)

1. Verifica cache (chave: `insights:{context}`, TTL 5 min)
2. Se cache hit, retorna direto sem chamar Gemini
3. Se cache miss, executa tools do contexto em paralelo (asyncio.gather)
4. Envia dados coletados ao Gemini com INSIGHTS_PROMPT
5. Parseia resposta JSON (trata markdown code fences)
6. Cacheia resultado e retorna

## Alertas Proativos (V2 — /alerts)

- Background task em `app/core/alerts.py` iniciada pelo lifespan da FastAPI
- Roda a cada `DEFAULT_REFRESH_SECONDS` (30 min); resultado vai para o cache
- Heuristicas (sem chamar Gemini, custo zero):
  - **delivery (high)**: pedidos PENDENTES com entrega nas proximas 48h
  - **production (medium)**: massas/recheios pendentes (limiar >= 5 itens)
  - **cancellation (high)**: taxa de cancelamento >= 20% (com volume minimo)
  - **batch (low/medium)**: fornada da vez com >= 80% de aproveitamento
- Endpoint `/api/v1/alerts` serve do cache; aceita `?refresh=true` para recalcular sob demanda

## Acoes via Chat (V2 — Agentic AI)

Tools que mudam estado (mark_order_as_*) seguem o padrao two-step:
1. Modelo chama com `confirmed=False` → tool retorna previa + `requires_confirmation: True`
2. Modelo apresenta a previa e aguarda confirmacao explicita do usuario
3. Modelo chama de novo com `confirmed=True` → tool executa o PATCH/POST

Ver `app/tools/AGENTS.md` para detalhes.

## Anti-patterns

- NUNCA acessar banco diretamente — toda leitura e via tools → backend Java
- NUNCA instanciar `httpx.AsyncClient` dentro de tools — usar `get_http_client()` do modulo compartilhado
- NUNCA hardcodar modelo Gemini — usar `settings.gemini_model` ou `model_manager.get_model()`
- NUNCA chamar `client.models.generate_content()` (sincrono) — usar `client.aio.models.generate_content()` (async)
- NUNCA expor detalhes de erro do Gemini pro usuario final — logar e retornar mensagem generica

## Smoke test (Backend + AI, sem abrir o app)

Com backend Java e AI service no ar, na pasta `ai-service`:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_stack.py
```

Inclui checagem de integracao **AI → Java** via `GET /api/v1/alerts?refresh=true` (as heuristicas chamam tools que batem no backend). Para tambem validar **Gemini + tools** (consome quota):

```powershell
.\.venv\Scripts\python.exe scripts\smoke_stack.py --with-ask
```

Opcional: `SMOKE_BEARER` com JWT se quiser repetir o mesmo header que o app usaria para endpoints protegidos.

## Testes

`pytest` com mocks. Rodar: `.venv/Scripts/python.exe -m pytest tests/ -v`
- `test_deps.py` — Sanitizacao, deteccao de injection e guardrails de conteudo
- `test_schemas.py` — Validacao Pydantic
- `test_routes.py` — Endpoints com mock do CarambolosAssistant
- `test_tools.py` — Registry, executores principais (dashboard) e tool de relatorio
- `test_actions_tool.py` — Feature 1: fluxo two-step das acoes (PATCH com confirmacao) e WhatsApp
- `test_deep_orders_tool.py` — Feature 2: filtros e detalhamento de pedidos
- `test_batches_and_catalog_tools.py` — Features 3 e 5: fornadas e catalogo
- `test_alerts.py` — Feature 4: heuristicas de alertas, cache e endpoint /alerts
- `test_model_manager.py` — Fallback, cooldown e liberacao quando todos em cooldown
- `test_order_ref.py` — Parse de numero de pedido (Pedido #X, `pedido 42`, etc.) para id do resumo
