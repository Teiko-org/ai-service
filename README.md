# Carambolos AI Service



Assistente Inteligente de Negocio para a confeitaria Carambolos.

Microsservico Python/FastAPI que usa Google Gemini com Function Calling para gerar insights a partir dos dados do backend Java existente.



Documentacao mais completa para agentes e desenvolvedores: **`AGENTS.md`** (raiz do `ai-service`) e **`app/tools/AGENTS.md`** (mapeamento tool → endpoint).



## Setup



```bash

cd ai-service

python -m venv .venv



# Windows

.venv\Scripts\activate



# Linux/Mac

source .venv/bin/activate



pip install -r requirements.txt

```



## Configuracao



Copie o `.env.example` para `.env` e preencha:



```bash

cp .env.example .env

```



| Variavel | Obrigatoria | Descricao |

|----------|-------------|-----------|

| `GEMINI_API_KEY` | Sim* | Uma chave do Google AI Studio (https://aistudio.google.com/) |
| `GEMINI_API_KEYS` | Sim* | Varias chaves separadas por virgula (ex.: 3 contas free); rota em 429/503 |

| `GEMINI_MODEL` | Nao | Modelo principal (default: `gemini-2.5-flash-lite`) |

| `GEMINI_FALLBACK_MODELS` | Nao | Modelos extras em 429/503, separados por virgula. Se vazio, usa a cadeia padrao em `app/core/model_catalog.py` |

| `CARAMBOLOS_API_URL` | Nao | URL do backend Java (default: `http://localhost:8080`) |

| `ALLOWED_ORIGINS` | Nao | CORS origins separados por virgula |

| `LOG_LEVEL` | Nao | DEBUG, INFO, WARNING, ERROR (default: `INFO`) |

| `ENABLE_WRITE_TOOLS` | Nao | `true` habilita escrita via assistente (fornada, pedido bolo). Default: `false` |

| `CONFIRM_TOKEN_SECRET` | Se writes ligadas | Segredo HMAC para tokens de confirmacao (obrigatorio com `ENABLE_WRITE_TOOLS=true`; use 24+ chars) |

| `CONFIRM_TOKEN_TTL_SECONDS` | Nao | Validade do token de confirmacao em segundos (default: `120`) |

| `ALLOW_ANONYMOUS_WRITES` | Nao | `true` so em dev local (app sem JWT). Em `ENVIRONMENT=production` o startup falha. |

| `ENVIRONMENT` | Nao | `development` (default) / `staging` / `production`. Usado para travar configuracoes inseguras. |

\* Use `GEMINI_API_KEY` **ou** `GEMINI_API_KEYS` (minimo uma chave). Com 3 chaves, em 429/503 o servico troca de modelo e depois de conta antes de falhar.

**Fallback Gemini:** em cada chamada, tenta a cadeia de modelos (padrao: `flash-lite` → `flash` → `3.1-flash-lite` → `3.5-flash` → `2.5-pro`); se esgotarem na mesma chave, passa para a proxima em `GEMINI_API_KEYS`. Status em `GET /api/v1/models-status` (`model_chain`, `model_primary`, cooldown por modelo e por chave). Para ver o que sua API key suporta: `python scripts/list_gemini_models.py --suggest-chain`.

## Rodar



```bash

uvicorn app.main:app --reload --port 8000

```



Swagger UI disponivel em http://localhost:8000/docs



## Endpoints



| Metodo | Rota | Descricao |

|--------|------|-----------|

| GET | `/api/v1/health` | Health check |

| POST | `/api/v1/ask` | Pergunta em linguagem natural (sessao, anexos PDF, `pending_confirmation` / commit via `confirmation`) |

| POST | `/api/v1/insights` | Insights proativos para dashboard (com cache) |

| GET | `/api/v1/alerts` | Alertas heuristicos (cache; opcional `?refresh=true`) |

| GET | `/api/v1/suggested-prompts` | Prompts pre-definidos (pills) |

| GET | `/api/v1/models-status` | Cadeia de modelos, cooldown por modelo e por API key (sem expor segredos) |



## Testes



```bash

pytest tests/ -v

```



## Arquitetura



```

app/

├── main.py              # FastAPI + CORS + rate limiting + lifespan

├── config.py            # Configuracao via .env + cadeia de modelos (GEMINI_FALLBACK_MODELS)

├── api/

│   ├── routes.py        # Endpoints (ask, insights, alerts, prompts, models-status)

│   └── deps.py          # Seguranca (auth, sanitizacao, prompt guard, guardrails)

├── core/

│   ├── assistant.py     # CarambolosAssistant (loop de function calling + fallback)

│   ├── prompts.py       # System prompt + instrucoes

│   ├── gemini.py        # Client Gemini (singleton)

│   ├── model_catalog.py # Cadeia padrao de modelos (texto + tools)
│   ├── model_manager.py # Fallback e cooldown entre modelos
│   ├── api_key_manager.py # Rotacao de chaves em 429/503

│   ├── alerts.py        # Heuristicas de alertas + cache

│   ├── sessions.py      # Sessoes de conversa in-memory com TTL e cleanup

│   ├── cache.py         # Cache in-memory thread-safe com TTL

│   ├── http_client.py   # Singleton httpx.AsyncClient compartilhado

│   ├── limiter.py       # Instancia slowapi para rate limiting

│   └── confirm_tokens.py # Tokens HMAC para confirmacao de escrita

├── tools/

│   ├── registry.py      # Registro e dispatch de todas as tools

│   ├── order_ref.py     # Normaliza Pedido #X / pedido X para id do resumo

│   ├── dashboard.py     # KPIs e metricas gerais

│   ├── orders.py        # Pedidos por periodo

│   ├── products.py      # KPIs de fornada

│   ├── production.py    # Producao pendente e entregas

│   ├── reports.py       # Tool sintetica de relatorio PDF

│   ├── actions.py       # Acoes PATCH/POST com confirmacao

│   ├── deep_orders.py   # Filtros e detalhes via resumo-pedido

│   ├── batches.py       # Consultas de fornadas

│   ├── catalog.py       # Cardapio / catalogo

│   └── writes/          # Escrita no backend (fornada, pedido bolo)

└── models/

    └── schemas.py       # Pydantic models (request/response)

```



## Funcionalidades principais



- **Function Calling** — O Gemini chama ferramentas que consultam a API do backend Java para obter dados reais

- **Fallback automatico de modelos** — Se um modelo esgotar a quota (429), o sistema troca automaticamente para o proximo disponivel

- **Sessoes de conversa** — Contexto mantido entre mensagens na mesma sessao (TTL 30 min)

- **Cache de insights** — Insights cacheados por 5 minutos para reduzir chamadas ao Gemini

- **Guardrails** — Protecao contra prompt injection, profanidade e conteudo fora de escopo; sanitizacao de dados do banco antes do LLM

- **Rate limiting** — Limites por endpoint via slowapi (por exemplo 15/min para `/ask`, 10/min para `/insights`)

- **Kuroko (leitura)** — Pedidos aprofundados, fornadas, catalogo, alertas heuristicos, relatorio PDF; numero `Pedido #X` normalizado via `order_ref.py`

- **Escrita com confirmacao** — Com `ENABLE_WRITE_TOOLS=true`: previa → confirmacao do usuario → commit (HMAC, throttle, idempotencia). O `/ask` pode devolver `pending_confirmation`; o app confirma com o campo `confirmation` no body (sem nova passada pelo Gemini). Tools: `create_batch`, `add_batch_lines`, `create_pedido_bolo_full`



## Scripts (opcional)

### Smoke rapido

- `scripts/smoke_stack.py` — backend + AI; `--with-ask` (Gemini) e `--with-writes` (preview de escrita, sem commit)

### Modelos Gemini

- `scripts/list_gemini_models.py` — lista modelos da sua chave; `--suggest-chain` sugere `GEMINI_MODEL` + `GEMINI_FALLBACK_MODELS`

### Roteiro de QA do assistente (Kuroko)

Roteiro manual: `scripts/ROTEIRO_KUROKO_ASSISTANTE.md`. Runner automatico (mesmo fluxo do app, via `POST /api/v1/ask`):

```powershell
cd ai-service
.\.venv\Scripts\python.exe scripts\run_roteiro.py --dry-run
.\.venv\Scripts\python.exe scripts\run_roteiro.py --read-only --delay 20
.\.venv\Scripts\python.exe scripts\run_roteiro.py --with-writes --auto-confirm --delay 20
```

| Flag | Uso |
|------|-----|
| `--dynamic-ids` (padrao) | Lista pedidos/massas/fornada no sistema e substitui IDs do roteiro |
| `--static-ids` + `--config` | IDs fixos em `scripts/roteiro_config.json` |
| `--from N --to M` | So um intervalo de passos |
| `--resume` | Mescla com `reports/roteiro_checkpoint.json` ou ultimo relatorio parcial |
| `--merge-from reports/roteiro_run_X.json` | Mescla com um relatorio anterior |

Saidas em `reports/` (gitignored): `roteiro_run_*.json` + `.md`, checkpoint a cada passo. Em crash, o relatorio parcial e salvo (`status`: `partial` / `crashed`). Limpeza: apague arquivos antigos em `reports/` quando nao precisar mais (mantenha o ultimo par `.json`/`.md`).

Detalhes e exemplos de retomada: secao 9 do `ROTEIRO_KUROKO_ASSISTANTE.md`.


