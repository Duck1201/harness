# Qwen3.5-4B versus Qwen2.5-Coder-7B-Instruct no Harness 2.0

> **Registro histórico, não roadmap.** Em 10 de agosto de 2026, Qwen2.5-Coder-7B
> foi removido do roadmap do Harness 2.0. As recomendações abaixo preservam a
> conclusão da pesquisa na data em que foi escrita e não criam RuntimeProfile,
> experimento obrigatório ou compromisso de release.

> Data da pesquisa: 9 de agosto de 2026  
> Escopo: uso local agentic/coding via Ollama, RX 7600 com 7,98 GiB de VRAM,
> 31 GiB de RAM, contexto de 24.576 tokens, oito tools tipadas e saída em
> português.

## Veredito

**Manter o Qwen3.5-4B local como baseline principal e cadastrar o
Qwen2.5-Coder-7B-Instruct como challenger obrigatório.** Não há evidência
comparável suficiente para trocar o modelo principal somente por leaderboard.

O Qwen2.5-Coder-7B-Instruct é o candidato mais promissor quando a tarefa é
predominantemente gerar, completar, reparar ou raciocinar sobre código. Ele tem
mais parâmetros, pós-treino especializado e suporte explícito a Fill-in-the-
Middle (FIM). Em contrapartida, o Qwen3.5-4B tem documentação e métricas agentic
mais atuais, thinking configurável, cobertura multilíngue declarada muito mais
ampla, visão e, no artefato local já medido, maior folga de memória e cerca de
68 tokens/s.

Para este projeto, **qualidade agentic é sucesso fim a fim**, não apenas código
correto em uma resposta isolada. Selecionar a tool certa, respeitar paths
relativos, persistir a alteração, não repetir chamadas e concluir em português
pesam mais do que HumanEval ou LiveCodeBench sozinhos.

Também não recomendo carregar os dois modelos simultaneamente na primeira
versão. Em uma GPU de 8 GiB, a troca ou convivência dos modelos e de seus caches
pode eliminar o ganho do especialista. Primeiro compare-os como perfis de
backend mutuamente exclusivos; só depois meça roteamento para um especialista.

## Ambiguidade do nome

Não foi encontrado um artefato oficial chamado **“Qwen Coder 2.7 7B”** nas
coleções da Qwen nem no catálogo do Ollama. A família oficial usa os tamanhos
0,5B, 1,5B, 3B, 7B, 14B e 32B; não há uma variante “2.7”. A interpretação mais
provável da pergunta é:

- checkpoint: `Qwen/Qwen2.5-Coder-7B-Instruct`;
- pacote local: `qwen2.5-coder:7b` no Ollama.

Esta pesquisa usa essa hipótese. Se “2.7” designar um fine-tune comunitário, as
conclusões de identidade, template, quantização e comportamento não podem ser
transferidas. Fontes: [família oficial Qwen2.5-Coder](https://qwenlm.github.io/blog/qwen2.5-coder-family/),
[model card do 7B Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct)
e [pacote do Ollama](https://ollama.com/library/qwen2.5-coder%3A7b).

## O que está realmente sendo comparado

Há três identidades que não podem ser confundidas:

| Identidade | Natureza | Evidência disponível |
|---|---|---|
| `mitos:latest` | Qwen3.5-4B abliterado, Q4_K_M, derivado de terceiro | aproximadamente 240 turnos locais herdados; 3,3–3,5 GB, 24.576 de contexto e cerca de 68 tokens/s |
| `Qwen/Qwen3.5-4B` | checkpoint oficial pós-treinado, BF16 e multimodal | especificações, template e benchmarks oficiais; não medido localmente neste projeto |
| `qwen2.5-coder:7b` | conversão Ollama Q4_K_M do Qwen2.5-Coder-7B-Instruct | artefato oficial do catálogo Ollama; ainda não medido no corpus do projeto |

Logo, a operação conhecida do `mitos` não prova o desempenho do Qwen3.5
oficial, e as alegações do Coder BF16 não provam o comportamento de sua
quantização Q4_K_M.

## Comparação para o harness

| Dimensão | Qwen3.5-4B | Qwen2.5-Coder-7B-Instruct | Consequência neste projeto |
|---|---|---|---|
| Especialização | modelo geral pós-treinado para linguagem, agentes e visão | modelo textual especializado em código | Coder tem vantagem provável em síntese/reparo; Qwen3.5 tem escopo mais alinhado à orquestração geral |
| Tamanho | 4B no language model; artefato oficial também inclui visão e MTP | 7,61B, sendo 6,53B sem embeddings | o Coder oferece mais capacidade, mas consome mais memória e tende a ser mais lento |
| Arquitetura | 32 camadas híbridas com Gated DeltaNet, atenção completa periódica e MTP | transformer denso Qwen2, 28 camadas, GQA 28Q/4KV, RoPE/SwiGLU/RMSNorm | Qwen3.5 foi desenhado para inferência/contexto eficientes; Coder é uma arquitetura mais madura no ecossistema local |
| Contexto upstream | 262.144 nativos | configuração nativa de 32.768; 128K requer YaRN | 24.576 cabe nos dois; os números máximos não decidem esta implantação |
| Tool calling | XML-like com tags `function`/`parameter`; parser `qwen3_coder` nos servidores recomendados | JSON dentro de `<tool_call>` XML | o formato do Coder é sintaticamente mais simples, mas ambos exigem adaptador e testes de round-trip |
| Thinking | pensa por padrão e oferece `enable_thinking`; o Ollama local usa `think:true` | não há modo thinking/non-thinking oficial documentado | Qwen3.5 permite trocar qualidade por latência, ao custo de mais estado e complexidade de parser |
| Código | 55,8 em LiveCodeBench v6 no card oficial | treinamento especializado em 5,5 trilhões de tokens relacionados a código e suporte a FIM | há forte razão para testar Coder, mas não há placar oficial diretamente comparável entre os dois |
| Agentic | card publica BFCL-v4 50,3, TAU2 79,9, VITA 22,0 e DeepPlanning 17,6 | o material oficial fala em Code Agents, mas não publica esses mesmos gates para o 7B | Qwen3.5 tem evidência agentic mais pertinente; nenhum desses números substitui as fixtures locais |
| Idiomas humanos | suporte declarado a 201 idiomas e dialetos | não foi localizado resultado específico de português para o Coder-7B | Qwen3.5 é a escolha documentalmente mais segura para conversa/saída em português |
| Modalidade | texto, imagem e vídeo | texto | visão permanece possível apenas no Qwen3.5, embora o artefato local ainda precise de validação |
| Ollama Q4 | artefato local medido em 3,3–3,5 GB | pacote oficial de 4,7 GB, Q4_K_M e 32K | o Coder deve caber em princípio, mas blob não é VRAM total; cache e overhead precisam ser medidos |
| Maturidade no projeto | baseline já observado em produção e benches | candidato sem dados locais | trocar agora descartaria a única linha de base medida |

Fontes da arquitetura e resultados do Qwen3.5:
[model card oficial](https://huggingface.co/Qwen/Qwen3.5-4B),
[`config.json`](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/config.json) e
[`tokenizer_config.json`](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/tokenizer_config.json).
Fontes do Coder:
[model card oficial](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct),
[`config.json`](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/config.json),
[`tokenizer_config.json`](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/tokenizer_config.json)
e [relatório técnico da Qwen](https://arxiv.org/abs/2409.12186).

## Tool calling e risco de integração

### Qwen2.5-Coder-7B

O template oficial injeta os schemas JSON dentro de `<tools>` e pede a forma:

```text
<tool_call>
{"name": "read_file", "arguments": {"file_path": "README.md"}}
</tool_call>
```

Os resultados retornam em `<tool_response>`. O template oficial do Ollama
repete esse contrato e manda não emitir outro texto junto da chamada. Isso é
mais simples de parsear do que os pares `<function=...>` e `<parameter=...>` do
Qwen3.5, mas não elimina estes riscos:

- JSON truncado ou com tipo divergente do schema;
- mais de uma chamada dentro do mesmo bloco;
- perda de correlação entre chamadas paralelas e respostas;
- colisão de conteúdo recuperado com `</tool_response>`;
- tool inexistente, path absoluto ou chamada repetida;
- diferenças entre o template Hugging Face e o template efetivamente instalado
  pelo Ollama.

O Coder também possui tokens oficiais de FIM (`fim_prefix`, `fim_suffix` e
`fim_middle`) e de representação de repositório (`repo_name` e `file_sep`). Isso
é uma vantagem concreta para autocomplete e infill. Porém, o Harness 2.0 opera
principalmente por `read_file`/`edit`/`write_file`; adotar FIM exigiria um
contrato de edição adicional e avaliação própria. Não se deve misturar FIM com
o loop de tools no primeiro teste. Fontes: [template do checkpoint](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/tokenizer_config.json)
e [template do pacote Ollama](https://ollama.com/library/qwen2.5-coder%3A7b/blobs/1e65450c3067).

### Qwen3.5-4B

O Qwen3.5 usa um formato mais novo, com `<function=nome>` e um bloco
`<parameter=nome>` para cada argumento. O vLLM recomendado pela Qwen exige
`--enable-auto-tool-choice --tool-call-parser qwen3_coder` e combina isso com o
parser de reasoning `qwen3`. O Ollama local declara renderer/parser Qwen3.5, mas
o projeto ainda precisa fixar sua versão e o hash do template.

O thinking permite planejamento antes da tool call, mas também aumenta tokens,
latência e superfície de falha do parser. O braço de reprodução deve continuar
com `think:true`; desligá-lo é um experimento separado, não uma correção.

### Contenção é independente do modelo

Nos dois casos, o harness deve continuar responsável por validar nome, schema,
path, SHA, autorização, orçamento e repetição. Um prompt nunca substitui uma
barreira mecânica.

O shell permanece **fora do baseline**, mas não é uma proibição arquitetural
permanente. `run_shell_command` só deve entrar em um perfil futuro depois de
sandbox isolado, aprovação/policy, limites, idempotência, auditoria e fixtures
específicas. O Coder ser especializado em código não reduz nenhum desses
requisitos.

## Contexto e memória na RX 7600

O Qwen2.5-Coder anuncia 131.072 tokens, mas seu `config.json` e tokenizer
oficiais estão configurados em 32.768. Para ir além disso, o model card manda
habilitar YaRN com fator 4 e alerta que YaRN estático pode prejudicar entradas
curtas. O pacote Ollama também registra 32.768. Portanto, 32K é o baseline
relevante; 128K é extrapolação configurada, não capacidade gratuita.

O contexto do projeto, 24.576, cabe nos dois modelos. Ainda assim, o Coder possui
atenção completa em 28 camadas, enquanto o Qwen3.5 usa uma arquitetura híbrida.
A inferência razoável é que o Coder deixará menos margem de VRAM para cache. Isso
precisa ser medido no runtime real, pois tamanho do arquivo não inclui KV cache,
buffers, compute e overhead do Ollama.

Comparação dos pesos:

- Qwen3.5-4B oficial BF16: 9.319.737.856 bytes, incluindo language model, visão
  e MTP ([índice oficial](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/model.safetensors.index.json));
- Qwen2.5-Coder-7B-Instruct BF16: 15.231.233.024 bytes
  ([índice oficial](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/blob/main/model.safetensors.index.json));
- Qwen2.5-Coder `Q4_K_M` no Ollama: 4,7 GB, 7,62B e 32K
  ([metadados do artefato](https://ollama.com/library/qwen2.5-coder%3A7b/blobs/60e05f210007));
- Qwen3.5 local `mitos`: 3,3–3,5 GB e 100% GPU nos testes herdados.

O Coder Q4_K_M é plausível na GPU de 7,98 GiB, mas não se deve prometer
offload zero em 24.576 tokens sem observar `ollama ps`, VRAM, throughput e
latência P95. A experiência anterior do projeto com modelos 8B/9B torna esse
gate especialmente importante.

## Código e benchmarks: o que pode e não pode ser concluído

O Coder foi continuadamente pré-treinado com 5,5 trilhões de tokens incluindo
código-fonte, grounding texto-código e dados sintéticos. A Qwen o posiciona para
geração, raciocínio, correção, completion e Code Agents. Isso sustenta a
**hipótese** de que o 7B superará o 4B em alterações de código difíceis.

Mas os placares publicados não formam um A/B limpo:

- o blog do Coder avaliou o Instruct em problemas LiveCodeBench de julho a
  novembro de 2024;
- o card Qwen3.5 publica LiveCodeBench **v6**, de outra geração e protocolo;
- versões de dataset, datas, prompts, sampling e modelo-juiz diferem;
- resultados destacados como Aider 73,7, McEval 65,9 e MdEval 75,2 são do
  **Coder 32B**, não do 7B;
- benchmarks de geração/reparo isolado não medem um loop multi-turn com oito
  tools, paths relativos e efeitos persistidos.

Assim, o 55,8 do Qwen3.5-4B em LiveCodeBench v6 não deve ser comparado
numericamente com a figura histórica do Coder-7B. Fontes:
[resultados Qwen3.5](https://huggingface.co/Qwen/Qwen3.5-4B#benchmark-results) e
[metodologia publicada para o Qwen2.5-Coder](https://qwenlm.github.io/blog/qwen2.5-coder-family/).

## Português e instruções operacionais

O Qwen3.5 declara suporte a 201 idiomas e dialetos e publica avaliações
multilíngues agregadas. O ecossistema Qwen2.5 geral inclui português, mas não foi
localizado um benchmark oficial específico de português para o
Qwen2.5-Coder-7B-Instruct. A alegação de várias linguagens no material Coder diz
respeito principalmente a **linguagens de programação**, não idiomas humanos.

Para os dois modelos, o desenho vigente continua adequado:

- system prompt e descrições de schemas em inglês;
- última instrução exigindo resposta final em português;
- nomes curtos ASCII em `snake_case`;
- paths relativos ao workspace;
- erro honesto e estruturado, com catálogo real de tools disponíveis.

O teste deve medir separadamente: adesão ao português, qualidade técnica e
obediência de sequência. Uma resposta em português não prova que a tool correta
foi usada.

## Perfil experimental sugerido para o Coder

Não reutilizar silenciosamente `temperature=0.3` como se fosse propriedade de
toda a família Qwen. O `generation_config.json` oficial do Coder registra
`temperature=0.7`, `top_p=0.8`, `top_k=20` e `repetition_penalty=1.1`.

O primeiro estudo deve ter dois braços de sampling para o Coder:

1. **perfil oficial:** 0,7 / 0,8 / 20 / 1,1;
2. **perfil alinhado ao baseline local:** temperatura 0,3, mantendo os demais
   campos explicitamente registrados.

Nos dois braços:

```yaml
model: qwen2.5-coder:7b@digest-fixado
runtime: ollama@versao-fixada
context_window: 24576
thinking: unsupported
stream_tool_calls: false
max_steps: 15
tools_on_final_step: false
tool_registry: as_mesmas_oito_tools
```

O digest, template, tokenizer, versão do Ollama e Modelfile precisam entrar no
manifesto antes de promover resultados. Fonte do sampling:
[`generation_config.json`](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/generation_config.json).

## A/B decisivo no corpus do projeto

Comparar o baseline `mitos` e o Coder com as mesmas fixtures, ordem balanceada,
seeds registradas e workspace descartável. Separar dois estratos:

1. **agentic:** escolha/encadeamento de tools, leitura antes de edição,
   persistência, paths, erros, repetição e conclusão;
2. **coding:** correção de patch, testes, reparo multi-arquivo, compreensão de
   repositório e regressões introduzidas.

Métricas mínimas:

- sucesso fim a fim e artefato final correto;
- validade do parser e dos argumentos;
- escolha da tool e sequência correta;
- tool inventada, no-op, path inválido e tentativa de escape;
- repetição idêntica e passos até concluir;
- taxa de escrita/edição realmente persistida;
- resposta final em português;
- tokens de prompt, resposta e thinking;
- TTFT, tokens/s, latência P50/P95 e duração total;
- pico de VRAM, percentual GPU/CPU e OOM;
- qualidade do patch e testes aprovados.

Aplicar o gate normativo já definido: pilotos pequenos só indicam direção;
promoção exige pelo menos 50 execuções por braço, três ordens/seeds e ausência
de regressão de segurança. Uma única violação de um gate crítico reprova o
braço.

## Regra de decisão

| Resultado local | Decisão |
|---|---|
| Coder melhora código isolado, mas piora tool loop, memória ou latência | manter Qwen3.5 como único modelo |
| Coder melhora sucesso fim a fim sem regressão crítica e cabe 100% na GPU | considerar Coder como modelo principal para este harness de coding |
| Coder melhora fortemente apenas patches difíceis e custo de troca é aceitável | usar como especialista opcional por tarefa, nunca por chamada individual |
| Qwen3.5 vence agentic e fica próximo em código | manter o baseline atual; simplicidade e folga operacional vencem |
| resultados variam com template/sampling | estabilizar manifestos e tuning antes de decidir modelo |

Minha previsão, explicitamente como **inferência a testar**, é:

- Qwen3.5-4B vencerá em velocidade, margem de contexto, português, visão e
  planejamento/tool use geral;
- Qwen2.5-Coder-7B vencerá em autocomplete/FIM e poderá vencer em geração e
  reparo de código mais difícil;
- no Harness 2.0 completo, o resultado dependerá de quanto a vantagem de código
  compensa a menor margem de VRAM e a ausência de evidência agentic equivalente.

Até esse A/B, a opção tecnicamente mais defensável é **Qwen3.5-4B como
orquestrador principal e Qwen2.5-Coder-7B-Instruct como challenger**, não uma
substituição imediata.

## Fontes primárias

### Qwen3.5-4B

- [Model card, arquitetura, benchmarks e serving](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Configuração oficial](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/config.json)
- [Tokenizer e template oficial](https://huggingface.co/Qwen/Qwen3.5-4B/raw/main/tokenizer_config.json)
- [Índice dos pesos BF16](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/model.safetensors.index.json)

### Qwen2.5-Coder-7B-Instruct

- [Model card oficial](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct)
- [Blog da família Qwen2.5-Coder](https://qwenlm.github.io/blog/qwen2.5-coder-family/)
- [Relatório técnico](https://arxiv.org/abs/2409.12186)
- [Configuração oficial](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/config.json)
- [Tokenizer, FIM e template de tools](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/tokenizer_config.json)
- [Configuração de geração](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/raw/main/generation_config.json)
- [Índice dos pesos BF16](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct/blob/main/model.safetensors.index.json)
- [Pacote Q4_K_M no Ollama](https://ollama.com/library/qwen2.5-coder%3A7b)
- [Template de tools/FIM do Ollama](https://ollama.com/library/qwen2.5-coder%3A7b/blobs/1e65450c3067)
- [Metadados do modelo Ollama](https://ollama.com/library/qwen2.5-coder%3A7b/blobs/60e05f210007)

### Evidência local usada apenas para contextualização

- [`HERANCA-PARA-O-2.0.md`](../../HERANCA-PARA-O-2.0.md)
- [`CRUZAMENTO_HERANCA_QWEN35_4B.md`](CRUZAMENTO_HERANCA_QWEN35_4B.md)
- [`DECISOES-2.0.md`](../DECISOES-2.0.md)
