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

| `CARAMBOLOS_API_URL` | Nao | URL do backend Java (default: `http://localhost:8080`) |

| `ALLOWED_ORIGINS` | Nao | CORS origins separados por virgula |

| `LOG_LEVEL` | Nao | DEBUG, INFO, WARNING, ERROR (default: `INFO`) |

| `ENABLE_WRITE_TOOLS` | Nao | `true` habilita escrita via assistente (fornada, pedido bolo). Default: `false` |

| `CONFIRM_TOKEN_SECRET` | Se writes ligadas | Segredo HMAC para tokens de confirmacao (obrigatorio com `ENABLE_WRITE_TOOLS=true`; use 24+ chars) |

| `CONFIRM_TOKEN_TTL_SECONDS` | Nao | Validade do token de confirmacao em segundos (default: `120`) |

| `ALLOW_ANONYMOUS_WRITES` | Nao | `true` so em dev local (app sem JWT). Em `ENVIRONMENT=production` o startup falha. |

| `ENVIRONMENT` | Nao | `development` (default) / `staging` / `production`. Usado para travar configuracoes inseguras. |

\* Use `GEMINI_API_KEY` **ou** `GEMINI_API_KEYS` (minimo uma chave). Com 3 chaves, em 429/503 o servico troca de modelo e depois de conta antes de falhar.

**Fallback Gemini:** por chamada, tenta modelos (`flash-lite` → `flash` → `pro`); se todos esgotarem na mesma chave, passa para a proxima em `GEMINI_API_KEYS`. Status em `GET /api/v1/models-status` (`models` + `api_keys`).

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

| GET | `/api/v1/models-status` | Cooldown de modelos e API keys (sem expor segredos) |



## Testes



```bash

pytest tests/ -v

```



## Arquitetura



```

app/

├── main.py              # FastAPI + CORS + rate limiting + lifespan

├── config.py            # Configuracao via .env + lista de modelos fallback

├── api/

│   ├── routes.py        # Endpoints (ask, insights, alerts, prompts, models-status)

│   └── deps.py          # Seguranca (auth, sanitizacao, prompt guard, guardrails)

├── core/

│   ├── assistant.py     # CarambolosAssistant (loop de function calling + fallback)

│   ├── prompts.py       # System prompt + instrucoes

│   ├── gemini.py        # Client Gemini (singleton)

│   ├── model_manager.py # Fallback e cooldown entre modelos

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



- `scripts/smoke_stack.py` — checagens rapidas contra backend e AI; flags `--with-ask` (Gemini) e `--with-writes` (preview de escrita, sem commit)


