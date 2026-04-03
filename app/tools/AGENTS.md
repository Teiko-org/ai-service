# Tools — Function Declarations do Gemini

Cada modulo neste diretorio define Function Declarations que o Gemini pode chamar e executores que traduzem essas chamadas em requests HTTP ao backend Java.

## Como adicionar uma nova tool

1. Escolha o modulo correto (ou crie um novo para um dominio novo):
   - `dashboard.py` — Metricas gerais, contagens, rankings, pedidos recentes
   - `orders.py` — Pedidos agrupados por periodo, itens por periodo (tendencias)
   - `products.py` — KPIs de fornada (aproveitamento, valor, por periodo, por ID)
   - `production.py` — Massas/recheios pendentes, entregas proximas

2. Adicione a `FunctionDeclaration` na lista `DECLARATIONS`:
   ```python
   genai.types.FunctionDeclaration(
       name="nome_snake_case",
       description="Descricao clara do que retorna — o Gemini usa isso pra decidir quando chamar",
       parameters=genai.types.Schema(
           type=genai.types.Type.OBJECT,
           properties={...},  # {} se nao tem parametros
           required=[...],     # omitir se nao tem required
       ),
   )
   ```

3. Adicione o tratamento no `execute()` do mesmo modulo. Assinatura obrigatoria:
   ```python
   async def execute(name: str, args: dict, base_url: str, token: str | None, client: httpx.AsyncClient) -> dict:
   ```

4. O `registry.py` auto-registra — basta importar o modulo novo la e adicionar ao `TOOL_DECLARATIONS` e `_EXECUTORS`.

## Contrato da funcao execute()

- Recebe `httpx.AsyncClient` compartilhado — NUNCA criar um novo
- Retorna `dict` (JSON serializavel)
- Status 204 do backend → retornar `{"data": [], "message": "Nenhum dado encontrado"}`
- Erros sao capturados pelo registry — nao precisa try/except dentro do executor
- Token Bearer e repassado ao backend via header Authorization

## Mapeamento tool → endpoint backend

### dashboard.py
| Tool                        | Endpoint backend                         |
|-----------------------------|------------------------------------------|
| get_unique_clients_count    | GET /dashboard/qtdClientesUnicos         |
| get_orders_count            | GET /dashboard/qtdPedidos                |
| get_cake_orders_count       | GET /dashboard/qtdPedidosBolo            |
| get_batch_orders_count      | GET /dashboard/qtdPedidosFornada         |
| get_top_products            | GET /dashboard/produtosMaisPedidos       |
| get_top_cakes               | GET /dashboard/bolosMaisPedidos          |
| get_top_batch_products      | GET /dashboard/produtosFornadasMaisPedidos|
| get_recent_orders           | GET /dashboard/ultimosPedidos            |

### orders.py
| Tool                        | Endpoint backend                              | Params             |
|-----------------------------|-----------------------------------------------|--------------------|
| get_cake_orders_by_period   | GET /dashboard/qtdPedidosBoloPorPeriodo       | periodo, ano       |
| get_batch_orders_by_period  | GET /dashboard/qtdPedidosFornadaPorPeriodo    | periodo, ano       |
| get_items_by_period         | GET /dashboard/itens-mais-pedidos-por-periodo | tipoItem, periodo, ano |

### products.py (KPIs fornada)
| Tool                        | Endpoint backend                          | Params             |
|-----------------------------|-------------------------------------------|--------------------|
| get_latest_batch_kpi        | GET /dashboard/kpi-fornada-mais-recente   | —                  |
| get_batch_kpi_by_period     | GET /dashboard/kpi-fornadas-por-periodo   | periodo, ano, mes  |
| get_batch_kpi_by_id         | GET /dashboard/kpi-fornada/{id}           | —                  |

### production.py
| Tool                        | Endpoint backend                          | Params             |
|-----------------------------|-------------------------------------------|--------------------|
| get_pending_doughs          | GET /dashboard/massas-pendentes           | —                  |
| get_pending_fillings        | GET /dashboard/recheios-pendentes         | —                  |
| get_upcoming_deliveries     | GET /dashboard/pedidos-proximos-entrega   | diasProximos       |

## Anti-patterns

- NUNCA usar `httpx.AsyncClient()` direto — o client vem como parametro via registry
- NUNCA retornar dados que nao vieram do backend — o Gemini nao deve receber dados inventados
- Descricao da FunctionDeclaration deve ser precisa — o Gemini decide qual tool chamar baseado NELA
- Nomes de tools em snake_case, parametros em snake_case (mapeados pra camelCase do backend dentro do executor)
