# Core — Logica do Assistente e Integracao Gemini

Nucleo do servico: client Gemini, loop de function calling, prompts, infra compartilhada.

## Modulos

| Arquivo          | Responsabilidade                                              |
|------------------|---------------------------------------------------------------|
| `assistant.py`   | `CarambolosAssistant` — orquestra ask() e generate_insights() |
| `gemini.py`      | Singleton do `genai.Client` (inicializado com API key)        |
| `prompts.py`     | SYSTEM_PROMPT e INSIGHTS_PROMPT (persona + regras + seguranca)|
| `http_client.py` | Singleton `httpx.AsyncClient` com lifecycle (cleanup no shutdown) |
| `limiter.py`     | Instancia do slowapi Limiter (importada pelas routes)         |

## Function Calling Loop (assistant.py)

O metodo `ask()` implementa o loop de function calling do Gemini:

1. Envia pergunta + TOOL_DECLARATIONS ao Gemini (async: `client.aio.models.generate_content`)
2. Se resposta contem `function_call` → executa via registry → envia `function_response` de volta
3. Repete ate max `MAX_TOOL_ROUNDS=5` ou Gemini responder com texto
4. O Gemini pode chamar multiplas tools num unico turno (parallel function calling)

O metodo `generate_insights()` e diferente: NAO usa function calling do Gemini. Ele chama as tools diretamente via `asyncio.gather()` (paralelo), concatena os dados, e pede ao Gemini pra analisar e gerar JSON de insights.

## Prompts (prompts.py)

- `SYSTEM_PROMPT` — Define persona, regras de resposta, contexto do negocio, e instrucoes anti-jailbreak. Modificar com cuidado: qualquer mudanca afeta TODAS as respostas.
- `INSIGHTS_PROMPT` — Template que pede insights em formato JSON especifico. O Gemini as vezes retorna com markdown code fence — o assistant.py trata isso no parse.

## Invariantes

- Todas as chamadas ao Gemini DEVEM ser async (`client.aio.models.generate_content`)
- Todas as chamadas ao Gemini DEVEM capturar `genai.errors.APIError` e converter em `RuntimeError` com mensagem
- `gemini.py` e `http_client.py` sao singletons — uma unica instancia por processo
- `http_client.py` e fechado via lifespan do FastAPI em `main.py` — se criar outro recurso com lifecycle, adicionar la
- O modelo Gemini vem de `settings.gemini_model`, NUNCA hardcodar

## Pitfalls

- `response.candidates` pode ser vazio (safety filter, quota) — sempre verificar antes de acessar
- Insights JSON do Gemini as vezes vem envolto em ```json ... ``` — o parser em assistant.py trata, mas se mudar o formato do INSIGHTS_PROMPT, verificar se o parse ainda funciona
- `MAX_TOOL_ROUNDS=5` existe pra evitar loop infinito — se o Gemini insistir em chamar tools apos 5 rounds, a resposta sai com o ultimo estado
