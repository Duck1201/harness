# Decisões normativas do Harness 2.0

> Versão: 2  
> Data: 10 de agosto de 2026  
> Estado: vigente

Este documento governa a primeira release. Os dossiês de herança, pesquisa e comparação preservam evidência e alternativas consideradas, mas não alteram o contrato vigente.

## Fontes executáveis e precedência

Os contratos consumíveis por código são:

- [`config/model-profiles.json`](../config/model-profiles.json), fonte de RuntimeProfiles e evidência de componentes;
- [`config/harness.json`](../config/harness.json), fonte de ExecutionRoutes, limites, policy, estado, stores, rede e UX;
- [`config/tool-registry.json`](../config/tool-registry.json), fonte única de `model_tools`, `internal_automations`, `prohibited_capabilities`, schemas e ResultPayload;
- [`evals/fixtures/regressions.json`](../evals/fixtures/regressions.json) e [`evals/experiments.json`](../evals/experiments.json), contratos versionados de avaliação;
- [`Modelfile`](../Modelfile), entrada da instalação Ollama local.

Em divergência, prevalecem: invariantes de segurança deste documento, contratos JSON, demais decisões desta página e, por último, documentos históricos. Alterar uma decisão exige atualizar contratos, fixtures e digests no mesmo conjunto.

## Vocabulário e fronteiras

O vocabulário canônico está em [`CONTEXT.md`](../CONTEXT.md). Em particular:

- RuntimeProfile identifica modelo, runtime, componentes embutidos, plataforma e capacidades observadas; não contém política de um Turn;
- ExecutionRoute seleciona um RuntimeProfile e fixa amostragem, tools, limites e reasoning para uma classe de execução;
- TerminalOutcome registra por que o Turn parou; TaskVerdict é o julgamento posterior do eval e nunca sobrescreve o desfecho;
- CanonicalHistory é estado autoritativo; ModelView e AG-UI são projeções derivadas.

## Baseline da primeira release

| Área | Decisão vigente |
|---|---|
| Runtime | Ollama local é o único runtime funcional; vLLM e remoto são candidatos sem rota ativa |
| Perfil ativo | `local_mitos_ollama_reproduction`, digest `aabca1b8777bc4e0a6a491fb5ab7288bcc1537bad35c74ff041879fa11705bff` |
| Instalação | O perfil instalado foi recriado e está coerente com o `Modelfile` de SHA-256 `fe816f3e5cc37ee82c381d6e5c6ac3cc187e1878f21c0b069f14dfba82559af9` |
| Componentes | Digest próprio ou evidência discriminada de componente embutido; ausência nunca é convertida em hash inventado |
| Tokenizer do estimador | `HuggingFaceTokenEstimator` lê um `tokenizer.json` local ao host, fixado por `host.json#tokenizer_digest` e registrado como componente `token_estimator_tokenizer_file`. Vocabulário e merges são idênticos aos do repositório upstream; os sete tokens de áudio/TTS a mais lá não ocorrem em texto, então a contagem é equivalente e os digests não. Como o arquivo não é versionado, esse digest é registro de procedência, não gate de CI |
| Capacidades | Cada capacidade declara `support`, `evidence` e `gate_status`; declaração de runtime não equivale a gate aprovado |
| Plataforma | Python 3.13, Linux x86_64 |
| ExecutionRoute | `local_web_tools`, web-first, sampling local `temperature=0.3`, `presence_penalty=0`, `think=true` |
| Loop | 15 AgentSteps, até 4 calls por AgentStep, 20 calls por Turn, 15 minutos e 8.192 tokens de saída |
| Execução de calls | Tool loop sem streaming; calls de um mesmo AgentStep são executadas em ordem, sem paralelismo |
| Último passo | Nenhuma tool é oferecida e o TerminalOutcome é persistido uma única vez |
| Tools | Somente `model_tools` são model-selectable; automações internas e capacidades proibidas são coleções separadas |
| Resultados | Todo ResultPayload contém `status`, `retryable`, `data`, `error` e `meta`; `blocked` só pode ser emitido pelo harness |
| Grants | Workspace requer WorkspaceRootGrant; escrita também exige WriteGrant; rede também exige WebAccessGrant |
| Rede | Toda operação web é efeito `data_egress`, negado por padrão e autorizado mecanicamente |
| Web | Brave Search e navegador Brave; HTTP pode anteceder browser dentro do executor, nunca por escolha do modelo |
| Isolamento | Cada operação usa contexto de navegador efêmero; web e verificação de página não compartilham estado |
| Contexto | Deduplicação, extração única de HTML e corte por orçamento; compressão de código desligada e experimental |
| Estado | CanonicalHistory completo em store conversacional; reasoning é transitório e nunca persistido |
| Stores | Dois bancos separados: estado canônico e telemetria sem conteúdo |
| Retenção | Uma policy global remove Conversation inteira; nunca cria buracos no histórico |
| UI | AG-UI é projeção do estado, não fonte canônica; UX e evals são web-first |
| Acesso | Sem senha de Operator, só loopback direto é atendido; com senha, toda rota exige sessão. Não há terceira opção |
| Confirmação | Escrita sob UntrustedWebTaint exige decisão do Operator para aquela chamada; aprovar não cria grant nem amplia acesso |
| Roadmap | Qwen2.5 está fora do roadmap; pesquisa comparativa permanece histórica |

## Estado, projeções e persistência

O store conversacional persiste PendingRequest, Turn, AgentStep, tool calls, ToolResult, resposta final e TerminalOutcome. Reasoning pode transitar ao vivo para a projeção AG-UI, mas não entra no CanonicalHistory, telemetria ou replay. ModelView é reconstruída antes de cada passo e pode deduplicar, extrair HTML e cortar blocos sem alterar o registro.

A telemetria usa outro store e recebe somente IDs, digests, classes, tamanhos, contagens e tempos. Falha de telemetria é não fatal. Retenção é uniforme e global; quando aplicada ao estado conversacional, sua unidade mínima é uma Conversation completa.

## Policy por efeitos

Nomes de tools não autorizam nada. A policy resolve os efeitos declarados no registry:

- `workspace_read` exige WorkspaceRootGrant;
- `workspace_write` exige WorkspaceRootGrant e WriteGrant;
- `data_egress` exige WebAccessGrant e controles de destino, DNS e redirect.

WebAccessGrant não é consentimento para backend remoto. Conteúdo obtido da web recebe UntrustedWebTaint, que acompanha derivações e nunca cria grant, confirmação ou permissão. Paths continuam relativos, canonicalizados, com symlinks resolvidos e confinados ao Workspace.

## Tools, automações e resultados

[`config/tool-registry.json`](../config/tool-registry.json) é a única fonte de nomes, schemas, efeitos, grants e envelopes. O registry contém exatamente três coleções conceituais:

1. `model_tools`: operações que podem aparecer para o modelo;
2. `internal_automations`: comportamentos controlados pelo harness;
3. `prohibited_capabilities`: efeitos que a primeira release não executa.

`blocked` significa que o harness recusou a operação por policy, grant, validação ou limite. Indisponibilidade ou recusa de provedor é `failed`, com classe em `error`; `empty` é sucesso sem itens. Nenhum deles pode virar string vazia ambígua.

## Web e browser

`web_search` usa Brave Search. `web_fetch` tenta HTTP guardado e pode escalar internamente para Brave por sintoma. Ambos são `data_egress`, revalidam SSRF em redirects e produzem dados com UntrustedWebTaint.

Cada execução de browser recebe contexto efêmero. A automação de PageRevision e o acesso web usam contextos distintos, sem cookies, cache, storage ou service workers compartilhados. O risco residual de DNS rebinding está documentado em [`RELEASE-PENDING.md`](RELEASE-PENDING.md).

## Verificação de página

Verificação de página é InternalAutomation pós-escrita e nunca tool do modelo. Sua chave de idempotência é PageRevision, composta por `workspace_id`, `relative_path`, `html_sha256`, `workspace_revision` e `verifier_digest`. O modo candidato `automatic_once_per_page_revision` começa desligado; promoção exige o experimento homônimo e atualização explícita deste contrato.

## Release e avaliações

A primeira superfície de produto é web e toda promoção exige tarefa fim a fim nessa superfície. Fixtures e experimentos declaram `dataset_version`, digest calculável e vínculo com RuntimeProfile e ExecutionRoute. Resultado de experimento pode ser ausente; inventar medição para preencher contrato é proibido.

O que não entra, seus motivos e gates está em [`RELEASE-PENDING.md`](RELEASE-PENDING.md). Isso inclui vLLM, remoto, visão, shell, streaming, paralelismo, compressão de código, branching, approve-with-edits, dark mode e a retirada de Qwen2.5 do roadmap.

## Evidência preservada

- [Herança empírica do Harness 1.0](../HERANCA-PARA-O-2.0.md)
- [Guia de arquitetura pesquisado](HARNESS_QWEN35_4B.md)
- [Guia histórico de tools](TOOLS_PARA_QWEN35.md)
- [Cruzamento entre herança e pesquisa](research/CRUZAMENTO_HERANCA_QWEN35_4B.md)
- [Pesquisa técnica do Qwen3.5-4B](research/QWEN35_4B_PESQUISA.md)
- [Comparativo histórico com Qwen2.5](research/COMPARATIVO_QWEN35_4B_VS_QWEN25_CODER_7B.md)
