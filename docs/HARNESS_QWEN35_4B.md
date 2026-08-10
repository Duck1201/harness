# Guia de arquitetura do harness para Qwen3.5-4B

> Status: base de projeto, pesquisada em 9 de agosto de 2026. As versões do
> modelo e dos runtimes mudam rapidamente; todo release do harness deve repetir
> a suíte de contrato descrita neste documento.
>
> **Status normativo atual:** evidência histórica. As decisões de release foram
> substituídas por [`DECISOES-2.0.md`](DECISOES-2.0.md) e
> [`RELEASE-PENDING.md`](RELEASE-PENDING.md). Em especial, vLLM, remoto, visão,
> streaming, paralelismo e compressão de código não entram na primeira release.

> **Evidência local incorporada:** este guia foi cruzado com
> [`HERANCA-PARA-O-2.0.md`](../HERANCA-PARA-O-2.0.md), que registra cerca de 150
> turnos de bancada e 90 turnos instrumentados de produção. Quando a recomendação
> oficial diverge do sistema medido, o perfil local vence para o 2.0; a
> generalização para o checkpoint oficial fica explicitamente pendente. Veja a
> análise completa em
> [`CRUZAMENTO_HERANCA_QWEN35_4B.md`](research/CRUZAMENTO_HERANCA_QWEN35_4B.md).

> **Contrato vigente:** implementação e revisão começam em
> [`DECISOES-2.0.md`](DECISOES-2.0.md). Perfis, flags e schemas são lidos de
> [`config/`](../config/); este guia explica a arquitetura, mas não duplica a
> fonte de verdade consumida pelo código.

## Dois perfis de evidência

| Perfil | O que sabemos | O que não pode ser presumido |
|---|---|---|
| `local_medido` | `mitos:latest`, descrito no dossiê como Qwen3.5-4B abliterado, 3,5 GB, Ollama, RX 7600 7,98 GB, contexto 24.576, ~68 tok/s | equivalência com pesos oficiais, quantização, template e safety do checkpoint canônico |
| `oficial_referencia` | `Qwen/Qwen3.5-4B`, BF16, multimodal, template XML-like, parsers e parâmetros publicados | desempenho na RX 7600, Ollama, quantização local e tarefas reais do projeto |

O rótulo mutável `latest` não é reprodutível. Antes da primeira bancada do 2.0,
registrar digest do modelo, quantização, `Modelfile`, tokenizer/template efetivo,
versão do Ollama e flags. Migrar para o checkpoint oficial ou outro quant cria
um novo braço experimental, não uma atualização transparente.

## Decisão resumida

O baseline recomendado é:

- `qwen3.5 4b` local como alvo e backend remoto como knob desde o primeiro dia;
- Ollama como baseline já medido; vLLM/SGLang como backends candidatos, nunca
  presumidos equivalentes;
- adaptador de capacidades por backend para thinking, multimodalidade, usage,
  streaming, template e tool calling;
- perfil local inicial com `temperature=0.3`, `presence_penalty=0` e contexto
  24.576; qualquer alteração vira braço de medição;
- tool loop **não streaming**; no Ollama, reproduzir primeiro o `think=true`
  medido e testar `false` como outro braço; no vLLM, começar com thinking
  desligado até a combinação reasoning+tools passar no gate de parser;
- respostas finais podem usar streaming depois de teste diferencial;
- template, tokenizer, runtime, parser e quantização sempre fixados e
  registrados juntos como uma única versão do sistema;
- LangGraph com `SqliteSaver`; histórico persistido é registro e o hook
  pré-modelo cria uma view derivada/deduplicada;
- schemas e exposição de tools derivados somente de
  [`tool-registry.json`](../config/tool-registry.json);
- nenhuma tool de shell no baseline atual e nenhuma tool de verificação de
  página exposta ao modelo;
- argumentos e resultados de tools são dados não confiáveis: validar schema,
  autorização e limites no harness antes de executar qualquer efeito.

No backend vLLM, quando ele for avaliado, a referência oficial continua sendo
`--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder`
e `--language-model-only` quando visão não estiver no escopo. Isso não transforma
vLLM no backend local já provado.

Essa configuração não trata thinking como propriedade universal. O dossiê
mostrou que um `think: true` literal prende o harness a uma família de modelos;
por isso o valor pertence ao perfil de capacidades do backend. No vLLM, também
há relatos de tool calls presas no conteúdo/raciocínio e não promovidas ao campo
estruturado pelo runtime ([vLLM #42021](https://github.com/vllm-project/vllm/issues/42021)).

## O que está sendo integrado

O Qwen3.5-4B é um modelo pós-treinado multimodal, não apenas um LLM de texto. O
card oficial o descreve como um modelo causal com encoder visual, 32 camadas,
dimensão oculta 2.560 e arquitetura híbrida: três blocos Gated DeltaNet para
cada bloco de atenção completa. O contexto nativo é de 262.144 tokens e a
extensão anunciada chega a 1.010.000 por YaRN. O artefato BF16 ocupa 9,34 GB no
Hugging Face; planejar memória apenas pelo nome “4B” é um erro. Fontes:
[model card](https://huggingface.co/Qwen/Qwen3.5-4B) e
[`config.json`](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/config.json).

O modelo inclui visão desde a origem. Se a aplicação for só de texto, o modo
`--language-model-only` do vLLM evita carregar/perfilar o encoder visual e deixa
mais memória para o cache. Se visão for necessária, o harness também terá de
normalizar URLs/base64, tamanho, número de imagens, frames de vídeo e orçamento
de tokens multimodais.

### Capacidade não é confiabilidade

O card publica, entre outros, estes resultados para o 4B:

| Dimensão | Benchmark | Resultado publicado |
|---|---:|---:|
| seleção de funções | BFCL-V4 | 50,3 |
| agente transacional | TAU2-Bench | 79,9 |
| agente geral | VITA-Bench | 22,0 |
| planejamento | DeepPlanning | 17,6 |
| contexto longo | LongBench v2 | 50,0 |
| contexto longo | AA-LCR | 57,0 |
| coding | LiveCodeBench v6 | 55,8 |
| agente de computador | OSWorld-Verified | 35,6 |

São números divulgados pelo próprio fornecedor, úteis como sinal e não como
SLA. Eles indicam uma assimetria importante: o modelo pode acertar muitos fluxos
bem definidos, mas não deve receber autonomia irrestrita para planejamento
longo, efeitos irreversíveis ou recuperação silenciosa de erros. O harness
precisa compensar com máquina de estados, validação e avaliações específicas.

### Prompt orienta; código garante

Nos testes locais, uma regra específica do system prompt — admitir que não podia
testar uma página e parar — foi desobedecida entre 33% e 78%, conforme braço.
Quando a capacidade de teste estava disponível, o braço observado caiu a 0%.
Isso não mede “desobediência geral” do Qwen: mede uma instrução, num checkpoint
abliterado, com amostras pequenas. Ainda assim, confirma a decisão arquitetural:
regra importante precisa de validador, transição de estado ou remoção mecânica da
ação; texto de prompt sozinho é preferência.

## Contrato real de chat e tools

### Não reutilizar o template do Qwen3

O template oficial do **Qwen3.5-4B** injeta os schemas JSON dentro de `<tools>` e
solicita uma forma XML semelhante à usada pelo Qwen3-Coder:

```text
<tool_call>
<function=read_file>
<parameter=file_path>
README.md
</parameter>
</function>
</tool_call>
```

O formato vem do template oficial; o contrato do executor local é mais estrito:
o valor é relativo ao workspace e paths absolutos são recusados. Explicitar isso
no schema eliminou erros de path de 7/18 para 0/18 turnos no sistema anterior.

Isso difere do Hermes/JSON documentado para o Qwen3 anterior. O runtime deve
usar o template que acompanha o checkpoint e o parser `qwen3_coder`, como
prescreve o [card oficial](https://huggingface.co/Qwen/Qwen3.5-4B). Não copiar
um template de blog, não montar ChatML manualmente e não assumir que “Qwen3” e
“Qwen3.5” têm o mesmo wire format. A fonte canônica é o
[`chat_template.jinja`](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/chat_template.jinja)
da revisão fixada.

### Invariantes que o adaptador de mensagens deve impor

1. A lista de mensagens não pode ser vazia.
2. `system`, quando existir, é a primeira mensagem e só pode aparecer uma vez.
3. Imagens e vídeos não são aceitos em `system` pelo template oficial.
4. `enable_thinking=false` ainda renderiza um bloco `<think></think>` vazio;
   portanto remover tags por regex antes do parser é perigoso.
5. Thinking histórico não volta à conversa. O próprio Qwen recomenda guardar
   apenas a resposta final em turnos anteriores.
6. Tool calls paralelas aparecem como blocos `<tool_call>` irmãos. A ordem deve
   ser preservada.
7. O XML nativo não contém um ID. O runtime normalmente cria `tool_call_id`; se
   houver parser próprio, o harness precisa gerar IDs únicos e manter a relação
   chamada–resultado.
8. Resultados adjacentes com role `tool` são agrupados pelo template dentro do
   turno de usuário. Não inventar uma role ou envelope diferente.
9. O orçamento total inclui system prompt, schemas de tools, histórico,
   conteúdo visual, resultados e geração reservada.

### Perfis de geração

Manter perfis explícitos e versionados, nunca parâmetros espalhados pelo código:

| Perfil | Thinking | Tools | Streaming | Uso inicial |
|---|---:|---:|---:|---|
| `local_tool_reproduction` | sim (`think:true`) | sim | não | reproduzir `mitos`/Ollama medido |
| `vllm_tool_parser_gate` | não | sim | não | validar integração oficial antes de qualidade |
| `answer_direct` | conforme capacidade | não | após teste | conversa sem efeitos |
| `reasoned_tools_experimental` | sim | sim | não | somente avaliação/canário |

Para o backend local, esses perfis devem nascer de um controle adicional:
`temperature=0.3` e `presence_penalty=0`. Em 18 turnos por braço, elevar
`presence_penalty` de 0 para 1,1 aumentou turnos sem tool call de 2 para 5,
reduziu `write_file` de 9 para 5 e diminuiu o arquivo entregue de 3.921 para
2.349 bytes. A hipótese causal é que penalizar tokens já presentes torna caro
colocar conteúdo longo dentro do argumento da tool. O resultado é específico do
`mitos`/Ollama, mas é evidência suficiente para sobrescrever o default oficial
no perfil local. `temperature=0.3` é a configuração medida, não uma ablação
isolada que prove superioridade.

As instruções operacionais e descrições de tools começam em inglês; uma linha
separada manda responder em português. Em 15 turnos por braço, a desobediência
medida caiu de 67% no prompt PT para 40% no EN e 14/14 respostas continuaram em
português. O inglês também tinha quatro linhas a menos, então idioma e concisão
estão confundidos: manter como baseline direcional e repetir a ablação no 2.0.

Parâmetros oficiais sugeridos pelo Qwen:

| Modo | `temperature` | `top_p` | `top_k` | `presence_penalty` |
|---|---:|---:|---:|---:|
| thinking geral | 1,0 | 0,95 | 20 | 1,5 |
| thinking para código preciso | 0,6 | 0,95 | 20 | 0,0 |
| não thinking geral | 0,7 | 0,8 | 20 | 1,5 |
| não thinking para raciocínio | 1,0 | 1,0 | 40 | 2,0 |

O fornecedor alerta que aumentar `presence_penalty` pode causar mistura de
idiomas e perda leve de qualidade. Para tool selection, testar também
temperaturas menores, mas não torná-las padrão sem medir: a configuração oficial
é o controle do experimento, não a verdade universal do produto.

Há ainda uma inconsistência no próprio card consultado: a tabela curta da seção
de API repete `1,0/0,95/20/1,5` para “não thinking com raciocínio”, enquanto a
seção posterior **Best Practices** usa `1,0/1,0/40/2,0`, reproduzida acima. O
harness deve fixar o perfil escolhido e seu source revision; este guia toma a
seção Best Practices como referência inicial e exige avaliação comparativa.

O card sugere até 32.768 tokens de saída para a maioria das consultas e 81.920
para problemas extremos. Um harness interativo deve impor limites menores por
rota e liberar esses tetos apenas quando necessário; do contrário, latência,
repetição e custo de contexto tornam-se imprevisíveis.

## Arquitetura do loop

```text
requisição
  -> normalização e orçamento de tokens
  -> registry aprovado filtrado apenas pela policy da sessão
  -> renderização pelo template fixado
  -> geração completa do turno
  -> parser do runtime + fallback de detecção (sem executar)
  -> validação estrutural, semântica, autorização e idempotência
  -> confirmação, quando houver efeito sensível
  -> execução isolada
  -> resultado limitado e serializado com segurança
  -> próximo turno ou resposta final
```

O fallback citado acima apenas **detecta** XML cru e transforma a falha em erro
observável. Ele não deve executar automaticamente uma chamada que o parser
principal rejeitou. Uma resposta ambígua pode misturar raciocínio, texto e
efeitos; “ser tolerante” aqui amplia o risco.

### Estado e limites

- Serializar turnos da mesma conversa.
- Usar o teto medido de 15 passos como baseline. No passo final, retirar todas
  as tools e pedir a resposta com o estado disponível; oferecer tool e pedir
  conclusão simultaneamente já produziu turnos sem resposta.
- Limitar também chamadas por turno, tempo por tool, bytes por resultado e
  tokens totais.
- Executar chamadas paralelas somente quando todas forem read-only,
  idempotentes e independentes; caso contrário, preservar a ordem.
- Repetir automaticamente apenas tools comprovadamente idempotentes. Após
  timeout de uma operação com efeito, consultar o estado ou pedir intervenção.
- Propagar `idempotency_key` para integrações que o suportem.
- Encerrar em: resposta final, limite atingido, erro não recuperável, negação de
  permissão ou confirmação recusada.
- Não permitir que o modelo aumente os próprios limites ou escolha tools fora
  do registry/policy efetivos da sessão.
- Cachear por turno chamadas idênticas para não repetir rede/efeito. Informar ao
  modelo que repetiu não é contenção: 27% das chamadas continuaram repetidas.
- Guardas são mecânicos: ação removida, chamada não reexecutada ou turno
  encerrado. Devolver um erro pedindo cooperação fez o modelo retentar e chegou
  a 12 chamadas no mesmo alvo apesar de um “teto” nominal de 5.
- Não habilitar ainda um teto por alvo como política própria: as duas tentativas
  de medir essa hipótese foram inválidas. O teto total de 15, sim, é baseline.

### Argumentos e resultados não confiáveis

Validar os argumentos novamente no harness mesmo que o runtime alegue ter
validado JSON Schema:

- tipos, obrigatórios, enums, formatos e campos desconhecidos;
- paths relativos após canonicalização, resolução de symlink e confinamento ao
  workspace; absoluto e escape são recusados;
- hosts, protocolos e faixas de rede para fetches;
- limites de tamanho, paginação e tempo;
- autorização do usuário e escopo da credencial.

Não existe tool de shell no perfil local. A jaula de arquivo não contém um
shell, e as 14 tentativas de inventá-lo no 1.0 não justificam entregar essa
capacidade. Quando o modelo chamar uma tool inexistente, o erro lista os nomes
reais; isso converteu invenção em correção no passo seguinte. Bloqueio, “zero
resultados” e falha são desfechos diferentes — nunca devolver string vazia para
os três.

O template insere o conteúdo de `tool` entre `<tool_response>` e
`</tool_response>` sem um mecanismo geral de escape. Logo, conteúdo externo que
traga tags como `</tool_response>`, `<tool_call>` ou `</think>` pode atacar a
estrutura do próximo prompt. Serializar resultados como um envelope JSON
compacto, escapar `<`, `>` e `&` (`\u003c`, `\u003e`, `\u0026`), truncar campos
grandes e rotular a origem como não confiável. Para blobs/código extenso,
retornar um identificador de artefato e metadados em vez do corpo integral.

Envelope sugerido:

```json
{
  "ok": true,
  "data": {},
  "error": null,
  "meta": {
    "tool": "read_file",
    "truncated": false,
    "untrusted": true
  }
}
```

O parser também usa tags `<parameter=...>` sem escaping completo. Incluir casos
com XML, Unicode, recuos e quebras de linha na suíte de round-trip.

## Contexto, memória e desempenho

### O “262k” não deve virar configuração padrão

O contexto nativo é 262.144 tokens, mas o cache de atenção, estados das camadas
lineares, pesos, buffers do encoder visual, batching e saída reservada competem
pela mesma memória. A capacidade máxima de um checkpoint não equivale a uma
configuração eficiente para produção.

Como aproximação conservadora, apenas o KV das oito camadas de atenção completa
em BF16 resulta em cerca de 32 KiB por token por sequência a partir do
`config.json` (8 camadas × 4 heads KV × 256 dimensões × K/V × 2 bytes): cerca de
8 GiB em 262k tokens, antes dos demais estados e overheads. O valor real deve ser
medido no runtime e hardware escolhidos; quantização de KV pode alterá-lo.

Prática recomendada:

- começar com os 24.576 tokens já medidos na RX 7600;
- tratar 32k/64k/128k/262k como perfis experimentais que precisam caber no
  hardware e demonstrar ganho;
- reservar explicitamente tokens de saída;
- remover thinking histórico da view, sem apagar o registro persistido;
- paginar tools de leitura e busca;
- expor métricas de tokens, cache e truncamento ao harness.

### Redução de contexto já medida

A ordem de implementação não é especulativa:

| Técnica | Evidência do 1.0 | Decisão do 2.0 |
|---|---:|---|
| dedup por referência | 1.279.397 → 19.933 tokens, **−98%** | primeiro mecanismo; ponteiro para a primeira ocorrência |
| extração de HTML | 36.723 → 3.853 tokens, **−90%** | uma função na camada de contexto, não duplicada na tool |
| corte por orçamento | necessário em 141/415 montagens, **34%** | view descarta mensagens antigas até caber; estado não é mutado |
| compressão de código | 99 blocos, **−28%** | manter com métrica por tipo |
| compressão mecânica de prosa | 4.818.608 → 4.797.226, arredondado **−0%** | não portar; sumarização/relevância seria outro experimento |

O `SqliteSaver` guarda o histórico integral. Dedup, extração, compressão e corte
produzem uma lista nova no hook pré-modelo; tentativa rejeitada continua no
registro para que o modelo veja o que fez.

O tokenizer local não inclui de forma confiável o overhead de chat e schemas.
Herdar o calibrador por backend: em 399 amostras ele reduziu o erro relativo a
1,8% e aprendeu overhead médio de 9,74 tokens por mensagem, contra o palpite
inicial de 4,0. A janela enviada ao backend e o orçamento do compressor são o
mesmo número; dois números independentes divergem em silêncio.

### YaRN até ~1M

YaRN é opt-in. A documentação oficial avisa que os runtimes implementam YaRN
estático e que o fator constante pode prejudicar textos curtos. Manter um pool
separado para contexto ultra-longo ou ativá-lo somente na rota que precisa; não
alterar permanentemente o `config.json` compartilhado. Avaliar retrieval e
agent loops no comprimento-alvo, porque os próprios benchmarks long-context do
4B estão longe de 100%.

O Qwen também aconselha pelo menos 128k para preservar thinking em tarefas
complexas. Isso é incompatível com o perfil local já medido de 24.576 numa GPU
de 8 GB. O 2.0 deve tornar essa perda potencial observável, não fingir que o
hardware local oferece o perfil recomendado: medir sucesso da tarefa em 24.576
e encaminhar casos que comprovadamente precisem de contexto maior a outro pool
ou backend consentido.

### Quantização

Quantizações comunitárias são abundantes, mas não são intercambiáveis. Para cada
artefato, registrar hash, método, bits, tokenizer/template empacotado e runtime.
Comparar contra BF16 em:

- perplexidade ou tarefas de linguagem do domínio;
- escolha/no-op de tools;
- exatidão byte a byte dos argumentos;
- planejamento multi-turno;
- português, código e visão, quando aplicável;
- throughput, latência e memória.

Não promover uma quantização apenas porque “carrega”. Há relatos recentes de
conversão e backend no ecossistema GGUF/Qwen3.5, inclusive expectativa incorreta
de camadas em um 4B convertido ([llama.cpp #24737](https://github.com/ggml-org/llama.cpp/issues/24737)).

## Runtimes e armadilhas conhecidas

### Ollama — baseline medido

É o único backend com números do projeto: `mitos:latest`, 3,5 GB, 100% na RX
7600, contexto 24.576 e cerca de 68 tok/s. O nome não basta para reprodução;
capturar digest, `ollama show`, `Modelfile`, versão e template efetivo.

O [`Modelfile`](../Modelfile) do projeto fixa `num_ctx=24576`,
`temperature=0.3`, `presence_penalty=0`, `top_k=20` e `top_p=0.8`. Ele não
sobrescreve `TEMPLATE`, `SYSTEM`, stops ou output budget: template/stops são
herdados e versionados com o artefato, enquanto system prompt, schemas, thinking
e limite de saída pertencem ao adaptador/rota. O hash do arquivo fica no perfil
`local_mitos_ollama_reproduction`.

A inspeção de 9 de agosto confirmou arquitetura `qwen35`, 4,5B parâmetros,
quantização `Q4_K_M`, renderer/parser `qwen3.5` e capacidades declaradas de
completion, vision, tools e thinking. Naquele momento, o `mitos:latest` ainda
refletia a versão anterior do arquivo e não mostrava `presence_penalty=0`. Essa
lacuna histórica foi encerrada em 10 de agosto: o contrato vigente registra a
instalação coerente e seu digest em [`model-profiles.json`](../config/model-profiles.json).

Essa separação segue a
[referência oficial do Modelfile](https://docs.ollama.com/modelfile), enquanto
`presence_penalty` é confirmado no
[`Options` oficial do Ollama](https://github.com/ollama/ollama/blob/main/api/types.go).
Como o parâmetro já apresentou regressões entre runners, a bancada ainda precisa
confirmar pelo comportamento/log do servidor que zero foi realmente aplicado.

O adaptador Ollama precisa expor capacidades em vez de literals no payload:
`supports_thinking`, `supports_tools`, `supports_vision`, `supports_streaming`,
`returns_usage` e o modo de desligar thinking. Erro `400 does not support
thinking` é incompatibilidade de configuração e não deve consumir tentativas do
modelo. Contagem/calibração de tokens é própria deste backend.

### vLLM

O card do modelo exige uma versão recente e mostra a configuração
`qwen3_coder`. Fixar wheel/container por versão e digest após passar nos testes;
“main” e “latest” não são versões de produção.

Problemas que justificam testes específicos:

- vLLM 0.10.0 a 0.10.1 tinha RCE no parser `qwen3_coder`; a correção foi em
  0.10.1.1 ([GHSA-79j6-g2m3-jgfw](https://github.com/vllm-project/vllm/security/advisories/GHSA-79j6-g2m3-jgfw)).
- Em 15/07/2026, o vLLM 0.25.1 ainda removia espaços iniciais/finais de valores
  em `qwen3_coder`/`qwen3_xml`, quebrando edits exatos e round-trip de código
  ([vLLM #48753](https://github.com/vllm-project/vllm/issues/48753)).
- Streaming já devolveu XML cru no campo de conteúdo em vez de `tool_calls`;
  usar não streaming no tool loop até a versão fixada passar no contrato
  ([vLLM #31871](https://github.com/vllm-project/vllm/issues/31871)).
- Há relatos de interação entre reasoning parser e tool parser que deixa a
  chamada dentro de `reasoning` ([vLLM #39056](https://github.com/vllm-project/vllm/issues/39056)).

Questões do GitHub são evidências de reproduções, não garantias de que toda
versão/hardware falha. Por isso o gate correto é a suíte local fixada.

### SGLang

É o segundo backend recomendado pelo card e usa o mesmo parser `qwen3_coder`.
Manter paridade de contrato com vLLM. Um relato no repositório Qwen observou
queda de aproximadamente 4,1% no C-Eval em configuração SGLang/vLLM sobre NPU;
o issue foi encerrado por inatividade, não resolvido tecnicamente
([Qwen #137](https://github.com/QwenLM/Qwen3.6/issues/137)). Tratar backend,
hardware e flags como parte do comportamento do modelo.

### Transformers

Útil como implementação de referência para renderizar o template e testes
moderados. O card originalmente exigiu a branch principal e também `torchvision`
e Pillow. Mesmo quando releases estáveis suportarem o modelo, fixar revisão. Não
usar a implementação de referência como oracle absoluto de desempenho.

### llama.cpp/GGUF

Bom candidato para implantação local e quantizada, mas o suporte ao modelo
híbrido, visão, template, reasoning e API compatível deve ser verificado no
commit exato. Não assumir equivalência com a API do vLLM, especialmente para
multimodal, streaming e tool calls.

## Automações e tools herdadas

### Verificação de página não é tool

O desenho candidato verifica automaticamente a página uma vez por SHA do
conteúdo e injeta o resultado uma vez. O modelo não recebe uma tool para pedir
nova verificação. A solução busca eliminar estruturalmente o loop medido
`write -> verificar -> mesmo erro -> rewrite`, que elevou latência de 18,6 para
194 s/turno, repetiu 61% das chamadas e matou 6/12 turnos no limite.

Esta arquitetura ainda é hipótese sem número no 2.0 e começa com
`page_verification.mode=disabled`. O contrato vigente generalizou a chave para
PageRevision e nomeia o braço `automatic_once_per_page_revision`, definido em
[`harness.json`](../config/harness.json).
Instrumentar taxa de correção, latência, repetições e encerramentos antes de
promovê-lo. O cache é por SHA dos bytes, nunca só pelo caminho: o 1.0 já retornou
descrição de screenshot antigo quando o arquivo foi reescrito no mesmo path.

### Web fetch decide o motor no código

O modelo pede o conteúdo; o harness tenta HTTP simples e escala para navegador
por sintoma. A decisão não é outra tool do modelo. Evidência herdada:

- Wikipédia entregou 160.820 caracteres por HTTP simples;
- sites JS testados exigiram navegador em 3/3, 3/3 e 4/4 casos;
- uma galeria oscilou: 12/30 exigiram navegador;
- menos de 120 caracteres extraídos é o limiar calibrado de página vazia;
- retries de carregamento usam 5/10/20/40/80 s; recusa não é retentável.

Cache de busca normaliza caixa/espaços; cache de URL é exato e vive só no
turno. Extração de HTML é uma única função na camada de contexto.

### Visão é evidência incerta

Descrição por modelo de visão é prefixada no resultado como estimativa e a
resposta final recebe aviso do harness quando visão foi usada. Um teste local
leu `IMC 24.7` como `IMC 247`; texto miúdo e números não podem ser tratados como
medição. O checkpoint oficial é multimodal, mas isso não prova que o artefato
quantizado local inclui projector nem que tenha a mesma qualidade.

### Falhas têm classes operacionais

- geração/tool call malformada entra no histórico para uma correção; se repetir
  duas vezes, subir como incompatibilidade de modelo/configuração;
- saldo, autenticação, rate limit e capability ausente sobem imediatamente;
- bloqueio, zero resultados e falha nunca compartilham uma string vazia;
- erro de tool é curto, estruturado, diz se é retentável e lista as tools reais
  quando o nome gerado não existe.

## Segurança mínima

- Não oferecer shell no baseline atual. Um perfil futuro pode introduzi-la, mas
  exige sandbox separado por conversa/tarefa, aprovação explícita, limites,
  auditoria, idempotência/reconciliação após timeout e gates de segurança.
- Workspace e rede negados por padrão; concessão por tool e por rota.
- Segredos nunca entram no prompt, schema, logs ou retorno de tool.
- Confirmação humana antes de apagar, publicar, enviar, comprar, conceder acesso
  ou modificar infraestrutura compartilhada.
- Separar `read_*` de `create_*`, `update_*` e `delete_*` também nas políticas.
- Limitar saída para evitar exfiltração e explosão de contexto.
- Considerar texto de web, arquivos e resultados como prompt injection.
- Registrar decisão, chamada, argumentos redigidos, autorização, resultado e
  duração em audit log.
- Não executar XML cru recuperado de fallback, texto do thinking ou fragmentos
  de streaming.
- Em fetches, resolver DNS, bloquear loopback/privado/link-local/reservado e
  multicast e revalidar cada redirect; registrar como lacuna a janela de DNS
  rebinding entre validação e conexão.
- Backend remoto é consentimento para exportar todo o contexto em cada passo,
  inclusive conteúdo de arquivo/página. Sem opt-in explícito, ele não existe.
- Métricas ficam em SQLite local e nunca armazenam conteúdo. LangSmith permanece
  atrás de knob desligado porque traces podem exportar prompt, resposta e dados.

## Compromissos do 2.0 já fechados

- **LangChain/LangGraph**, sem portar código do 1.0.
- **`SqliteSaver`** como fonte do estado conversacional completo.
- **Hook pré-modelo** para criar a view deduplicada, comprimida e cortada sem
  tocar no registro.
- **SQLite local sem conteúdo** como fonte de verdade das métricas; falha de
  telemetria avisa e se desliga, nunca derruba o turno.
- **AG-UI nativo** e telemetria própria em eventos `CUSTOM`.
- **assistant-ui** no painel; componente por tool em registry.
- Documentação em português; texto operacional consumido pelo modelo começa em
  inglês.
- [`model-profiles.json`](../config/model-profiles.json) fixa identidade,
  sampling e capacidades; [`tool-registry.json`](../config/tool-registry.json)
  é a única fonte de tools e schemas.
- Sem RAG/embeddings, seleção dinâmica de tools ou retenção parcial até existir
  demanda medida. Retenção, se vier, remove conversa inteira por idade.

Eventos mínimos: chamada de tool, montagem de contexto, calibração, turno, rede
e uso. Todo evento leva turno/passo; o desfecho do turno é gravado no caminho de
encerramento garantido, inclusive erro e abandono. Tokens são persistidos por
categoria e backend, não custo em moeda, porque preço muda.

## Suíte de contrato antes de implementar features

Os casos iniciais estão materializados em
[`evals/fixtures/regressions.json`](../evals/fixtures/regressions.json); os
braços e gates estão em
[`evals/experiments.json`](../evals/experiments.json). A lista abaixo explica a
cobertura esperada.

### Renderização e parsing

- Snapshot do template/tokenizer da revisão fixada.
- Turnos system/user/assistant válidos e mensagens inválidas.
- Thinking ligado/desligado e remoção do thinking histórico.
- Uma tool, nenhuma tool, tool inexistente e tool forçada.
- Duas tool calls paralelas e dois passos sequenciais.
- Resultados simples, múltiplos, vazios, com erro e truncados.
- Argumentos com recuo inicial, espaço final, newline final, tabs, aspas,
  barras, Unicode/emoji, JSON aninhado, XML e tags sentinela.
- Comparação streaming/não streaming sem executar efeitos.
- Round-trip `mensagens -> template -> geração simulada -> parser -> mensagens`.

### Seleção e planejamento

- Pares fáceis de confundir: `read_file`/`list_directory`,
  `glob`/`grep_search`, `web_search`/`web_fetch`.
- Pedido que não requer tool e pedido que explicitamente proíbe tool.
- Campos obrigatórios ausentes, enums, datas e números.
- Catálogo completo inicial de oito tools derivado do registry; só testar
  5/10/20/40 e roteamento dinâmico quando o registry crescer e houver problema
  medido.
- Português, inglês e mistura dos dois.
- Recuperação após erro observável sem repetir efeitos.
- Prompt operacional EN versus PT com mesmo conteúdo e mesmo número de linhas,
  isolando o confound do teste anterior.
- `presence_penalty` 0 versus valores oficiais, medindo tool no-op, chamadas de
  escrita, bytes entregues e conclusão fim a fim.

### Segurança

- Prompt injection vindo de página, arquivo e resultado de tool.
- `../`, symlink, caminho absoluto e race de arquivo.
- SSRF, redirects e URLs privadas.
- Tentativa de inventar shell e de exfiltrar variável secreta.
- Timeout após efeito aplicado e replay duplicado.
- Tool não autorizada e argumentos extras.
- No braço candidato, verificação automática exatamente uma vez por SHA de HTML
  e nunca como tool; o controle mantém a automação desligada.

### Métricas e gates

- acurácia de seleção e de no-op;
- taxa de parse válido;
- exact match e schema match dos argumentos;
- sucesso fim a fim por tarefa;
- chamadas extras e passos até concluir;
- violações de autorização/segurança (gate deve ser zero);
- tokens de prompt, thinking, resposta e tool results;
- TTFT, latência p50/p95, tokens/s, RAM/VRAM e cache;
- repetição, mistura de idioma e truncamento.

Promover uma combinação checkpoint/runtime/parser/template/quantização apenas
quando ela superar os gates. Uma atualização de qualquer elemento cria uma nova
combinação candidata.

## Ordem de implementação sugerida

1. Preencher o manifesto de `model-profiles.json`, validar o registry e congelar
   a versão das fixtures.
2. Schema de eventos, SQLite local sem conteúdo e desfecho garantido do turno.
3. LangGraph + `SqliteSaver` e hook pré-modelo com dedup, HTML, orçamento e
   calibrador.
4. Adaptador de mensagens/template por backend e suíte de contrato.
5. Registry real com as oito tools aprovadas, paths relativos, schemas em inglês e
   validação/autorização; shell permanece adiada.
6. Loop não streaming, `temperature=0.3`, `presence_penalty=0`, teto mecânico de
   15 e cache por turno.
7. Experimento de verificação automática por SHA, ainda desligada por default,
   e `web_fetch` com escalada interna.
8. AG-UI + servidor de replay de conversa gravada + assistant-ui.
9. Repetir as bancadas reais para thinking, idioma, parâmetros, nomes e
   checkpoint oficial/quantizações.
10. Streaming, visão nativa, paralelismo, backend remoto e contexto longo, cada
    um atrás de consentimento, flag e gate próprio.

Bancadas usam sessões/banco/workspace próprios, path curto e comum e tarefas
extraídas de falhas reais. Com 9–15 turnos por braço, relatar direção e intervalo,
não vender diferença de um erro como conclusão.

## Fontes centrais

- [Decisões normativas do Harness 2.0](DECISOES-2.0.md)
- [Perfis executáveis de modelo/backend](../config/model-profiles.json)
- [Registry canônico de tools](../config/tool-registry.json)
- [Fixtures e experimentos](../evals/README.md)
- [Herança empírica do harness 1.0](../HERANCA-PARA-O-2.0.md)
- [Cruzamento entre herança e pesquisa oficial](research/CRUZAMENTO_HERANCA_QWEN35_4B.md)
- [Qwen/Qwen3.5-4B — model card e recomendações](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Configuração oficial do checkpoint](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/config.json)
- [Template oficial de chat/tools](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/chat_template.jinja)
- [Tokenizer e tokens de controle](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/tokenizer_config.json)
- [Qwen-Agent](https://github.com/QwenLM/Qwen-Agent)
- [Catálogo de tools do Qwen Code](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/tools/tool-names.ts)
