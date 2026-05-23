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
- `app/tools/` — Function Declarations do Gemini mapeadas a endpoints do backend. Inclui `order_ref.py` (normaliza numero do pedido #X / pedido X para id do resumo) e `writes/` (V3 — criacao com two-step HMAC). Ver `app/tools/AGENTS.md`.
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
| GET    | /api/v1/models-status      | 60/min      | Nao     | `model_chain`, cooldown (`models`, `api_keys`) |

## Configuracao

Tudo via `.env` / variavel de ambiente. Validado por `pydantic-settings` em `app/config.py`:
- `GEMINI_API_KEY` ou `GEMINI_API_KEYS` (virgula, ex. 3 chaves) — Google AI Studio; rotacao em 429/503
- `GEMINI_MODEL` — Modelo Gemini principal (default: `gemini-2.5-flash-lite`)
- `GEMINI_FALLBACK_MODELS` — Lista opcional (virgula) de modelos apos o principal; padrao em `app/core/model_catalog.py`
- `CARAMBOLOS_API_URL` — URL do backend Java (default: `http://localhost:8080`)
- `ALLOWED_ORIGINS` — CORS origins separados por virgula
- `LOG_LEVEL` — DEBUG, INFO, WARNING, ERROR
- `ENABLE_WRITE_TOOLS` — `true` carrega tools de escrita no registry (default: `false`)
- `CONFIRM_TOKEN_SECRET` — Obrigatorio se writes ligadas; segredo HMAC do preview-token (use 24+ chars; <24 dispara warning no startup)
- `CONFIRM_TOKEN_TTL_SECONDS` — TTL do token (default: `120`)
- `ALLOW_ANONYMOUS_WRITES` — `true` so em dev local (app sem JWT); em `ENVIRONMENT=production` o startup falha
- `ENVIRONMENT` — `development` | `staging` | `production` (controla o guard acima)

Cadeia padrao (`DEFAULT_GEMINI_FALLBACK_MODELS` em `app/core/model_catalog.py`, sobrescrita por `GEMINI_FALLBACK_MODELS` no `.env`):
- `gemini-2.5-flash-lite` → `gemini-2.5-flash` → `gemini-3.1-flash-lite` → `gemini-3.5-flash` → `gemini-2.5-pro`

Ordem efetiva: `GEMINI_MODEL` primeiro, depois os fallbacks sem duplicar. Listar modelos da chave: `scripts/list_gemini_models.py`.

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
8. Loop ate max **5** rounds (leitura) ou **8** quando aparece tool de escrita (`create_*`, `add_*`, PATCH de `actions`)
9. Se alguma tool retornar `requires_confirmation`, a resposta inclui `pending_confirmation` (action, confirm_token, payload, message)
10. Mensagens (user + assistant) sao salvas na sessao
11. Response com `answer`, `tools_used`, `session_id` e opcionalmente `pending_confirmation` / `attachments`

**Commit direto (G13):** o app pode enviar `confirmation` no body do `/ask` (sem Gemini) com o mesmo `confirm_token` e `payload` da previa — ver `WriteConfirmationCommit` em `schemas.py`.

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
- Dedupe por `(type, title)` e no maximo **5** alertas por resposta
- Endpoint `/api/v1/alerts` serve do cache; aceita `?refresh=true` para recalcular sob demanda

## Acoes via Chat (V2 — status / WhatsApp)

Tools `mark_order_as_*` e `generate_whatsapp_message` em `actions.py`. PATCH de status segue two-step com `confirmed` (sem HMAC na V2; writes V3 usam `confirm_token`).

## Escrita via Chat (V3 — criacao de entidades)

Ativas so com `ENABLE_WRITE_TOOLS=true`. Tools em `app/tools/writes/`:

| Tool | Efeito |
|------|--------|
| `create_batch` | `POST /fornadas` |
| `add_batch_lines` | `POST /fornadas/da-vez` (lote, aceita `produto_nome`) |
| `close_batch` | Encerra uma ou varias fornadas (`fornada_ids`) |
| `replace_active_batch` | Encerra ativa + cria nova em uma confirmacao |
| `create_pedido_bolo_full` | Cadeia recheio → bolo → pedido → resumo (+ endereco se ENTREGA); aceita `massa_nome`, `recheio_nome`, `recheio_exclusivo_nome` |

**Glossario de IDs de pedido (V3):**

- `pedido_numero` (resposta de `create_pedido_bolo_full` e de `get_cake_order_details`) = id do resumo = **Pedido #X** no Kanban/app. Unico numero que aparece para o usuario.
- `ids_internos.pedido_bolo_id` / `numeroPedido` no JSON do Java = ids de entidades internas. Nunca citar ao usuario.
- O assistente armazena `last_pedido_resumo_id` na sessao apos commit, permitindo "detalhes do pedido que criamos".

Fluxo two-step (todas as writes):

1. `confirmed=false` (ou omitido) → previa + `confirm_token` HMAC (TTL ~120s, ligado a sessao e payload)
2. Nova mensagem do usuario (ou botao Confirmar no app)
3. `confirmed=true` + mesmo `confirm_token` + mesmo payload → commit (idempotente 5 min em retry)

Guardrails: auth obrigatorio no commit, throttle por sessao+tool, validacao local, `sanitize_for_llm` nas leituras, rollback best-effort na cadeia de bolo.

Ver `app/tools/AGENTS.md` (secao `writes/`).

## Politica de idioma (prompts e descriptions)

Centralizada em `app/core/prompt_contracts.py`. Resumo:

- Respostas ao usuario, `SYSTEM_PROMPT`, previas de write e `instruction` em tool results: **PT-BR**, persona unica **Kuroko**, sem acentos.
- Nomes de tools / parametros: **EN snake_case** (estavel, acoplado a testes e backend).
- `FunctionDeclaration.description`: **PT-BR**, formato curto "o que faz + quando usar".
- `INSIGHTS_PROMPT`: chaves JSON em **EN** (contrato com o parser/UI).

Piloto de descriptions em EN em tools read-only fica fora do ciclo V3 — abrir como experimento separado, com roteiro `scripts/ROTEIRO_KUROKO_ASSISTANTE.md` rodado antes e depois para medir regressao.

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

Preview de write V3 (sem Gemini, sem commit no Java; exige `ENABLE_WRITE_TOOLS` + `CONFIRM_TOKEN_SECRET` no `.env`):

```powershell
.\.venv\Scripts\python.exe scripts\smoke_stack.py --with-writes
```

Opcional: `SMOKE_BEARER` com JWT se quiser repetir o mesmo header que o app usaria para endpoints protegidos.

## Roteiro automatizado (regressao do assistente)

`scripts/run_roteiro.py` executa `scripts/ROTEIRO_KUROKO_ASSISTANTE.md` contra `POST /api/v1/ask` (leituras, escrita V2/V3, demo fornada). Usa as mesmas rotas de fallback de modelo/chave que o app.

```powershell
.\.venv\Scripts\python.exe scripts\run_roteiro.py --read-only --delay 20
.\.venv\Scripts\python.exe scripts\run_roteiro.py --with-writes --auto-confirm --from 20 --resume --delay 20
```

- `--dynamic-ids` (padrao): probes de listagem antes do run; substitui IDs/datas de exemplo.
- `--auto-confirm`: confirma previas de escrita (token ou `Confirmo.` na sessao).
- `--resume` / `--merge-from`: une passos em um unico `roteiro_run_*.json`; checkpoint em `reports/roteiro_checkpoint.json` a cada passo.
- Heuristicas em `run_roteiro.py` marcam FAIL se escrita ficar so na previa sem tool de sucesso.

Relatorios em `reports/` (gitignored). Roteiro completo: `scripts/ROTEIRO_KUROKO_ASSISTANTE.md` secao 9.

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
- `test_confirm_tokens.py`, `test_write_throttle.py`, `test_sanitize_for_llm.py`, `test_assistant_hardening.py` — Guardrails V3 fase 0
- `test_writes_fornada.py`, `test_writes_pedido_bolo.py`, `test_write_idempotency.py` — Writes V3 fases 1–2
- `test_alerts_dedupe.py` — Dedupe de alertas
- `test_run_roteiro_evaluate.py`, `test_run_roteiro_merge.py`, `test_roteiro_parser.py`, `test_roteiro_resolve.py` — Runner do roteiro e IDs dinamicos
