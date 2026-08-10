# Nomes e schemas de tools para Qwen3.5-4B

> Guia de convenções e catálogo inicial. Pesquisado em 9 de agosto de 2026.
>
> **Status normativo atual:** evidência histórica. A fonte vigente é
> [`config/tool-registry.json`](../config/tool-registry.json), com as coleções
> `model_tools`, `internal_automations` e `prohibited_capabilities` e policy por
> efeitos.

## Resposta curta

Não existe um nome mágico que garanta function calling. Para o Qwen3.5-4B, a
melhor base disponível é reaproveitar o vocabulário do **Qwen Code oficial**
quando a semântica for a mesma, usar nomes ASCII em `snake_case`, começar com
verbo explícito e manter o catálogo pequeno por turno.

Para o perfil local herdado, há regras mais fortes que a convenção genérica:

- schemas e instruções que o modelo lê devem começar em inglês, com resposta
  final em português;
- paths de tools são **relativos ao workspace** e o schema deve dizer que paths
  absolutos são recusados;
- `run_shell_command` e `code_interpreter` não são oferecidas no baseline: a
  jaula de paths não contém execução arbitrária; shell pode entrar futuramente
  após cumprir gates próprios;
- verificação de página é um efeito automático pós-escrita do harness, não uma
  tool selecionável pelo modelo;
- com oito tools não houve demanda medida para seleção dinâmica ou
  `tool_search`;
- erro deve distinguir bloqueio, vazio e falha e listar as tools realmente
  disponíveis quando o modelo inventar um nome.

Esses overrides vêm dos testes descritos em
[`HERANCA-PARA-O-2.0.md`](../HERANCA-PARA-O-2.0.md), não de uma recomendação
oficial do Qwen. O cruzamento e seus limites de generalização estão em
[`CRUZAMENTO_HERANCA_QWEN35_4B.md`](research/CRUZAMENTO_HERANCA_QWEN35_4B.md).
O contrato consumido pela implementação é
[`config/tool-registry.json`](../config/tool-registry.json); tabelas e exemplos
deste guia explicam o contrato, mas não são uma segunda fonte de schemas.

Regex portátil recomendada:

```regex
^[a-z][a-z0-9_]{0,62}$
```

O limite conservador de 63 caracteres é a interseção prática entre o SDK do
Qwen Code (1–64 caracteres, início por letra, alfanuméricos e `_`) e sua camada
MCP (trunca nomes acima de 63 e sanitiza caracteres). Fontes:
[SDK](https://github.com/QwenLM/qwen-code/blob/main/packages/sdk-typescript/README.md)
e [integração MCP](https://github.com/QwenLM/qwen-code/blob/main/docs/developers/tools/mcp-server.md).

## Regras de nomenclatura

### 1. `verbo_objeto`

O nome deve antecipar ação e alvo sem depender da descrição:

- `read_file`, não `file` ou `reader`;
- `list_directory`, não `filesystem`;
- `search_orders`, não `order_tool`;
- `cancel_order`, não `manage_order`;
- `validate_address`, não `process`.

### 2. Verbos têm semânticas fixas

| Verbo | Contrato esperado | Exemplo |
|---|---|---|
| `get` | um recurso conhecido por ID/chave | `get_order` |
| `list` | coleção limitada, sem busca semântica | `list_orders` |
| `search` | consulta/filtro que pode retornar candidatos | `search_orders` |
| `read` | lê conteúdo sem efeito | `read_file` |
| `create` | cria recurso novo; tem efeito | `create_issue` |
| `update` | altera recurso existente | `update_order` |
| `delete` | exclusão destrutiva | `delete_file` |
| `archive` | remoção reversível/não destrutiva | `archive_order` |
| `run` | inicia execução delimitada | `run_report` |
| `validate` | verifica sem alterar | `validate_address` |

Não misturar `get`, `fetch`, `read` e `load` como sinônimos dentro do mesmo
catálogo. Escolher um contrato e mantê-lo.

### 3. Uma tool, uma responsabilidade observável

Evitar `manage_files(action=...)` ou `database(operation=...)`. Eles escondem
efeitos no argumento, dificultam seleção, allowlists e confirmação. É preferível
ter `read_file`, `write_file` e `delete_file`, com políticas independentes.

### 4. Sem aliases simultâneos

Não expor `grep`, `search_file_content` e `grep_search` ao mesmo tempo. O próprio
Qwen Code migrou `search_file_content` para `grep_search` e `replace` para
`edit`; manter aliases apenas na borda de compatibilidade, invisíveis ao modelo.
Fonte: [`tool-names.ts`](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/tools/tool-names.ts).

### 5. O efeito deve estar no nome

Nomes genéricos como `send`, `apply`, `process`, `handle`, `execute`, `action`,
`tool` e `do_task` são ruins porque não revelam alvo nem risco. Para operações
sensíveis, preferir `send_email`, `publish_release`, `delete_database_backup`.

### 6. Namespaces só quando necessários

Para tools de negócio próprias, `github_create_issue` é mais fácil de entender
que `create_issue` quando há várias plataformas. Em MCP, colisões podem virar
`serverName__toolName` automaticamente no Qwen Code; não dependa desse nome
gerado como API estável. Resolva colisões no registry e teste o nome final que o
modelo realmente recebe.

## Catálogo aprovado para o Harness 2.0

O primeiro release expõe exatamente as oito tools abaixo. A lista é derivada do
registry, nunca copiada manualmente para o prompt.

| Nome | Contrato público | Controle principal |
|---|---|---|
| `read_file` | ler intervalo de arquivo conhecido | paginação e path relativo |
| `write_file` | criar ou substituir arquivo inteiro | SHA obrigatório para substituir |
| `edit` | substituir intervalo de linhas | SHA e round-trip byte a byte |
| `list_directory` | listar entradas imediatas | paginação e path relativo |
| `glob` | localizar paths por padrão | retorna paths, não conteúdo |
| `grep_search` | procurar texto/regex em arquivos | retorna matches limitados |
| `web_search` | descobrir páginas atuais | resultado limitado e classificado |
| `web_fetch` | obter URL pública conhecida | HTTP primeiro; browser interno por sintoma |

O conjunto é pequeno o bastante para ser enviado integralmente, sujeito apenas
à policy de autorização da sessão. Não implementar roteamento semântico nem
`tool_search` enquanto uma avaliação 5/10/20/40 não localizar degradação causada
pelo tamanho do catálogo.

### Tools condicionais ou adiadas

| Nome | Gate para entrar |
|---|---|
| `run_shell_command` | sandbox isolado, aprovação explícita, limites, auditoria, idempotência e evals de segurança |
| `delete_file` | requisito explícito de produto e política de confirmação |
| `describe_image` | visão nativa testada no artefato local exato |
| `agent` | demanda medida por delegação e isolamento de estado |
| `tool_search` | degradação medida com registry maior |
| `todo_write` | benefício medido em tarefas longas |
| `monitor` | existência de processos longos controlados |
| `ask_user_question` | protocolo de interação e bloqueio definido |

### Tools proibidas ou internas

| Nome/capacidade | Disposição |
|---|---|
| `code_interpreter` | proibida; execução arbitrária está fora do threat model |
| `verify_page` | interna e automática somente no braço histórico; o contrato vigente usa PageRevision e `automatic_once_per_page_revision` |
| `browser` | motor interno de `web_fetch` |
| `web_extractor` | operação única da camada de contexto |

O fato de alguns desses nomes aparecerem no Qwen Code oficial prova apenas
compatibilidade de vocabulário. Não os torna adequados ao risco ou ao produto.

### Tools de negócio

Aplicar os mesmos padrões:

| Necessidade | Bom nome | Evitar |
|---|---|---|
| pedido por ID | `get_order` | `order` |
| procurar pedidos | `search_orders` | `find` |
| criar reembolso | `create_refund` | `refund` |
| cancelar pedido | `cancel_order` | `update_order` com action |
| cotar frete sem comprar | `quote_shipping` | `ship_order` |
| efetivar envio | `create_shipment` | `quote_shipping` com flag |
| validar endereço | `validate_address` | `process_address` |
| enviar mensagem | `send_message` | `message` |
| consultar saldo | `get_account_balance` | `account` |
| transferir fundos | `create_transfer` | `update_balance` |

Separar consulta de efeito é mais importante do que economizar uma tool.

## Nomes dos parâmetros

Parâmetros também compõem a linguagem que o modelo precisa aprender. Usar o
mesmo nome para o mesmo conceito em todas as tools.

| Conceito | Nome recomendado | Nota |
|---|---|---|
| caminho de arquivo | `file_path` | não alternar com `path` |
| caminho de pasta | `directory_path` | distingue de arquivo |
| padrão glob/regex | `pattern` | descrição diz qual sintaxe |
| busca humana | `query` | não usar para ID exato |
| URL | `url` | schema restringe protocolos |
| início de página | `offset` | declarar base 0 ou cursor |
| tamanho máximo | `limit` | impor teto no servidor |
| cursor opaco | `cursor` | não misturar com offset |
| timeout | `timeout_ms` | unidade no nome |
| data/hora | `timestamp` | exigir ISO 8601 na descrição |
| ID de recurso | `order_id`, `issue_id` | alvo explícito |
| repetição segura | `idempotency_key` | em operações com efeito |

No caso de `read_file`, manter `file_path`, `offset` e `limit`, como os exemplos
oficiais do prompt do Qwen Code. Exposição repetida do mesmo vocabulário nos
exemplos, schemas e resultados reduz traduções mentais desnecessárias.

## Como escrever o schema

O template do Qwen3.5 injeta o objeto da tool como JSON no system prompt. Nome,
descrição e schema consomem contexto a cada turno e são parte efetiva do prompt.

### Princípios

- Descrição começa pelo que a tool faz, depois “use quando” e “não use quando”.
- Declarar efeito, necessidade de confirmação e formato do retorno.
- Schema raso, tipos simples e poucos campos obrigatórios.
- `enum` para vocabulário fechado; não pedir que o modelo memorize strings na
  descrição.
- Declarar formato de datas, paths, regex e unidades no parâmetro.
- Não criar booleanos que trocam a identidade da operação, como
  `delete=true` em uma tool de update.
- Campos desconhecidos devem ser rejeitados no harness, mesmo que algum backend
  remova `additionalProperties` ao adaptar o schema.
- Defaults são aplicados pelo executor, nunca presumidos a partir de ausência
  ambígua.
- Erro de tool é dado estruturado, não exceção ou stack trace despejada no
  prompt.

### Exemplo: leitura

Este é um recorte explicativo. O objeto completo e vigente é lido de
[`config/tool-registry.json`](../config/tool-registry.json); a implementação não
deve copiar este bloco.

```json
{
  "type": "function",
  "function": {
    "name": "read_file",
    "description": "Reads a range from an existing text file. Use when the file is already known. Do not use it to find files; use glob.",
    "parameters": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "file_path": {
          "type": "string",
          "description": "Path relative to the allowed workspace. Absolute paths and paths that escape the workspace are rejected."
        },
        "offset": {
          "type": "integer",
          "minimum": 0,
          "description": "First line, zero-based. Defaults to 0."
        },
        "limit": {
          "type": "integer",
          "minimum": 1,
          "maximum": 2000,
          "description": "Maximum number of lines. Defaults to 500."
        }
      },
      "required": ["file_path"]
    }
  }
}
```

### Exemplo: efeito de negócio

```json
{
  "type": "function",
  "function": {
    "name": "cancel_order",
    "description": "Cancels an order that is still cancelable. This changes state and requires user confirmation. Do not use it to check order status.",
    "parameters": {
      "type": "object",
      "properties": {
        "order_id": {
          "type": "string",
          "description": "Exact order identifier."
        },
        "reason": {
          "type": "string",
          "enum": ["customer_request", "duplicate", "fraud", "other"]
        },
        "idempotency_key": {
          "type": "string",
          "description": "Unique key supplied by the harness to prevent duplicate cancellation."
        }
      },
      "required": ["order_id", "reason", "idempotency_key"]
    }
  }
}
```

## Pares que precisam de descrição contrastiva

### `glob` versus `grep_search`

- `glob`: procura **nomes/caminhos** por padrão; retorna paths.
- `grep_search`: procura **conteúdo** dentro de arquivos; retorna matches com
  arquivo e posição.

### `web_search` versus `web_fetch`

- `web_search`: descobre URLs a partir de uma consulta.
- `web_fetch`: obtém o conteúdo de uma URL já conhecida.

### `read_file` versus `list_directory`

- `read_file`: conteúdo de um arquivo conhecido.
- `list_directory`: entradas imediatas de uma pasta.

### `write_file` versus `edit`

- `write_file`: cria ou substitui um arquivo inteiro; efeito mais amplo.
- `edit`: altera trecho delimitado; deve falhar se a pré-condição não bater.

Os quatro pares devem aparecer em casos negativos do eval: não basta testar a
tool correta quando ela é a única opção.

## Limitações específicas do formato Qwen3.5

O template representa parâmetros assim:

```text
<parameter=nome>
valor
</parameter>
```

Consequências:

- nomes com `<`, `>`, espaços, Unicode exótico ou pontuação aumentam a
  ambiguidade; por isso ASCII `snake_case`;
- valores contendo tags de fechamento exigem testes e possível codificação;
- código sensível a whitespace depende de o parser preservar recuos/newlines;
- streaming fragmenta tags e argumentos, portanto não executar antes do bloco
  completo;
- tool call XML pode aparecer como texto cru se o parser do backend falhar.

Em 15/07/2026, um bug aberto do vLLM 0.25.1 removia whitespace significativo em
argumentos `qwen3_coder`/`qwen3_xml`, incluindo `old_string`/`new_string` de
edits ([vLLM #48753](https://github.com/vllm-project/vllm/issues/48753)). Para
tools de código, exigir um teste byte a byte no runtime escolhido. Como defesa
adicional, aceitar edição por intervalo + hash de pré-condição reduz dependência
de correspondência cega, embora o conteúdo novo ainda precise de round-trip.

## Anti-padrões

| Anti-padrão | Problema | Correção |
|---|---|---|
| `tool1`, `do_it` | nenhuma semântica | `validate_address` |
| `manage_order(action)` | efeitos escondidos | `get_order`, `cancel_order` |
| `search` | domínio indefinido | `grep_search`, `web_search` |
| `get_or_create_user` | leitura e efeito juntos | duas tools |
| `readFile`/`ReadFile` misturados | convenção instável | `read_file` |
| nomes traduzidos por idioma | duplica escolha | nomes em inglês; no perfil local, descrição model-facing em inglês |
| `github.create-issue` | menor portabilidade | `github_create_issue` |
| descrição “faz coisas com arquivos” | seleção ambígua | declarar ação, uso e não uso |
| retorno livre enorme | prompt injection/contexto | envelope JSON paginado |
| 50 tools em todo turno | seleção e contexto maiores | medir degradação; só então avaliar roteador/`tool_search` |

## Avaliação dos nomes

O corpus inicial está em
[`evals/fixtures/regressions.json`](../evals/fixtures/regressions.json), e os
braços controlados estão em
[`evals/experiments.json`](../evals/experiments.json). Antes de congelar uma
mudança de nome, ele deve cobrir:

1. exemplos positivos de cada tool;
2. exemplos negativos em que nenhuma tool deve ser chamada;
3. todos os pares confundíveis;
4. nomes alternativos A/B com o mesmo schema/descrição;
5. português e inglês;
6. catálogos de tamanhos crescentes;
7. tool chamada correta, argumentos válidos e conclusão fim a fim.

Métricas mínimas:

- acurácia de seleção;
- falso positivo de tool;
- taxa de nome inexistente;
- schema/exact match de argumentos;
- número de passos e chamadas desnecessárias;
- sucesso fim a fim;
- tokens consumidos pelos schemas.

O nome vencedor é o que melhora a tarefa completa sem aumentar chamadas
perigosas — não necessariamente o mais curto nem o que parece mais elegante.

## Catálogo oficial consultado — somente referência

O [arquivo de constantes do Qwen Code](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/tools/tool-names.ts)
lista, entre outros: `edit`, `write_file`, `read_file`, `zoom_image`,
`grep_search`, `glob`, `run_shell_command`, `todo_write`, `save_memory`, `agent`,
`skill`, `web_fetch`, `web_search`, `image_gen`, `list_directory`, `lsp`,
`ask_user_question`, `send_message`, `monitor`, `notebook_edit`, `tool_search`,
`read_mcp_resource`, `display_image`, `get_goal` e `update_goal`.

Esse catálogo é referência de linguagem, não uma recomendação de expor tudo ao
Qwen3.5-4B em cada chamada.
