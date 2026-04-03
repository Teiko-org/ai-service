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

A `GEMINI_API_KEY` e obrigatoria. Obtenha gratuitamente em https://aistudio.google.com/

## Rodar

```bash
uvicorn app.main:app --reload --port 8081
```

Swagger UI disponivel em http://localhost:8081/docs

## Endpoints

| Metodo | Rota | Descricao |
|--------|------|-----------|
| GET | `/api/v1/health` | Health check |
| POST | `/api/v1/ask` | Pergunta em linguagem natural |
| POST | `/api/v1/insights` | Insights proativos para dashboard |

## Testes

```bash
pytest tests/ -v
```

## Arquitetura

```
app/
├── main.py           # FastAPI + CORS + rate limiting
├── config.py         # Configuracao via .env
├── api/
│   ├── routes.py     # Endpoints REST
│   └── deps.py       # Seguranca (auth, sanitizacao, prompt guard)
├── core/
│   ├── assistant.py  # CarambolosAssistant (loop de function calling)
│   ├── prompts.py    # System prompt + instrucoes
│   └── gemini.py     # Client Gemini (singleton)
├── tools/
│   ├── registry.py   # Registro e dispatch de todas as tools
│   ├── dashboard.py  # KPIs e metricas gerais
│   ├── orders.py     # Pedidos por periodo
│   ├── products.py   # Fornadas e KPIs
│   └── production.py # Producao pendente e entregas
└── models/
    └── schemas.py    # Pydantic models (request/response)
```
