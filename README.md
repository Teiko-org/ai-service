# Carambolos AI Service

Assistente Inteligente de Negocio para a confeitaria Carambolos.
Microsservico Python/FastAPI que usa Google Gemini com Function Calling para gerar insights a partir dos dados do backend Java existente.

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
| `GEMINI_API_KEY` | Sim | Chave do Google AI Studio (https://aistudio.google.com/) |
| `GEMINI_MODEL` | Nao | Modelo principal (default: `gemini-2.5-flash-lite`) |
| `CARAMBOLOS_API_URL` | Nao | URL do backend Java (default: `http://localhost:8080`) |
| `ALLOWED_ORIGINS` | Nao | CORS origins separados por virgula |
| `LOG_LEVEL` | Nao | DEBUG, INFO, WARNING, ERROR (default: `INFO`) |

## Rodar

```bash
uvicorn app.main:app --reload --port 8000
```

Swagger UI disponivel em http://localhost:8000/docs

## Endpoints

| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/ask` | Pergunta em linguagem natural (com sessao) |
| POST | `/api/v1/insights` | Insights proativos para dashboard (com cache) |
| GET | `/api/v1/suggested-prompts` | Prompts pre-definidos (pills) |
| GET | `/api/v1/models-status` | Status dos modelos Gemini (cooldown/disponibilidade) |

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
│   ├── routes.py        # Endpoints REST (ask, insights, prompts, models-status)
│   └── deps.py          # Seguranca (auth, sanitizacao, prompt guard, guardrails)
├── core/
│   ├── assistant.py     # CarambolosAssistant (loop de function calling + fallback)
│   ├── prompts.py       # System prompt + instrucoes
│   ├── gemini.py        # Client Gemini (singleton)
│   ├── model_manager.py # Gerenciador de modelos com fallback e cooldown
│   ├── sessions.py      # Sessoes de conversa in-memory com TTL e cleanup
│   ├── cache.py         # Cache in-memory thread-safe com TTL
│   ├── http_client.py   # Singleton httpx.AsyncClient compartilhado
│   └── limiter.py       # Instancia slowapi para rate limiting
├── tools/
│   ├── registry.py      # Registro e dispatch de todas as tools
│   ├── dashboard.py     # KPIs e metricas gerais
│   ├── orders.py        # Pedidos por periodo
│   ├── products.py      # Fornadas e KPIs
│   └── production.py    # Producao pendente e entregas
└── models/
    └── schemas.py       # Pydantic models (request/response)
```

## Funcionalidades principais

- **Function Calling** — O Gemini chama ferramentas que consultam a API do backend Java para obter dados reais
- **Fallback automatico de modelos** — Se um modelo esgotar a quota (429), o sistema troca automaticamente para o proximo disponivel
- **Sessoes de conversa** — Contexto mantido entre mensagens na mesma sessao (TTL 30 min)
- **Cache de insights** — Insights sao cacheados por 5 minutos para reduzir chamadas ao Gemini
- **Guardrails** — Protecao contra prompt injection, profanidade e conteudo fora de escopo
- **Rate limiting** — Limites por endpoint via slowapi (15/min para /ask, 10/min para /insights)
