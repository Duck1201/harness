# Qwen3.5-4B: pesquisa técnica para um harness agentic

**Data da pesquisa:** 9 de agosto de 2026  
**Modelo principal:** `Qwen/Qwen3.5-4B`  
**Escopo:** inferência local/servida, chat, reasoning, multimodalidade, tool calling, robustez de protocolo, segurança e avaliação.  
**Critério de fontes:** documentação, model cards, configuração e código dos projetos que mantêm o modelo ou os runtimes. Issues de upstream aparecem somente como evidência de riscos concretos, com o estado e a abrangência explicitados.

> **Complemento empírico local:** este relatório caracteriza principalmente o
> checkpoint oficial. Os resultados do harness 1.0 usam `mitos:latest`, uma
> variante abliterada/quantizada servida por Ollama, e por isso não foram
> misturados silenciosamente aos fatos oficiais. O confronto ponto a ponto está
> em [`CRUZAMENTO_HERANCA_QWEN35_4B.md`](CRUZAMENTO_HERANCA_QWEN35_4B.md); as
> decisões normativas aplicadas ao 2.0 estão em
> [`DECISOES-2.0.md`](../DECISOES-2.0.md), e os schemas vigentes estão somente em
> [`config/tool-registry.json`](../../config/tool-registry.json). As tabelas de
> catálogo e baseline deste relatório são referências oficiais genéricas, não o
> catálogo local aprovado.

## Como ler o nível de certeza

- **Fato documentado:** afirmação explícita em fonte oficial ou diretamente observável nos artefatos oficiais.
- **Inferência técnica:** conclusão derivada de configuração, formato ou aritmética; precisa ser validada no hardware e backend escolhidos.
- **Evidência de upstream:** comportamento reproduzido/reportado no repositório oficial de um runtime; não implica automaticamente que toda versão ou o 4B sejam afetados.
- **Lacuna:** não foi encontrada evidência primária suficiente.

## Resumo executivo

O `Qwen/Qwen3.5-4B` é o checkpoint **pós-treinado**, multimodal e conversacional da família Qwen3.5. Apesar do nome “4B”, ele inclui um encoder visual e uma cabeça de Multi-Token Prediction (MTP); o índice oficial soma **9.319.737.856 bytes de pesos** e a interface do Hub o classifica como aproximadamente 5B parâmetros no artefato inteiro. Não deve ser confundido com `Qwen/Qwen3.5-4B-Base`, destinado principalmente a fine-tuning e pesquisa, não a interação direta. Ambos usam Apache 2.0. ([model card](https://huggingface.co/Qwen/Qwen3.5-4B), [índice de pesos](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/model.safetensors.index.json), [Base](https://huggingface.co/Qwen/Qwen3.5-4B-Base), [licença](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/LICENSE); acesso em 2026-08-09.)

Para um harness, os pontos decisivos são:

1. O modelo **pensa por padrão**. O modo sem pensamento é habilitado via `chat_template_kwargs: {enable_thinking: false}` em vLLM/SGLang, mas via `enable_thinking: false` no Alibaba Cloud Model Studio. Os comandos textuais `/think` e `/nothink` não são oficialmente suportados no Qwen3.5. ([model card, modos](https://huggingface.co/Qwen/Qwen3.5-4B#instruct-or-non-thinking-mode); acesso em 2026-08-09.)
2. A saída de tool calling nativa do template não é JSON: é um protocolo XML-like com `<tool_call>`, `<function=...>` e `<parameter=...>`. O harness deve tratar o adaptador do backend e o parser como componentes versionados e testados, não como simples transporte. ([template oficial](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json); acesso em 2026-08-09.)
3. Para vLLM, a recomendação oficial é `--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder`; SGLang usa `--reasoning-parser qwen3 --tool-call-parser qwen3_coder`. A própria página do modelo ainda pede versões main/nightly para compatibilidade, portanto fixar commit/imagem e executar testes de contrato em upgrades é obrigatório. ([serving oficial](https://huggingface.co/Qwen/Qwen3.5-4B#serving-qwen35); acesso em 2026-08-09.)
4. O contexto nativo é 262.144 tokens, extensível oficialmente a 1.010.000 com YaRN. Isso não significa que seja barato: apenas o KV das oito camadas de atenção completa é estimado em cerca de **8 GiB por sequência** em BF16 a 262K, antes de pesos, estados lineares, ativações e encoder visual. Começar com 32K–64K é operacionalmente mais seguro; aumentar só com métricas e necessidade demonstrada.
5. O 4B é forte para o porte, mas não é um orquestrador infalível. Nos números publicados pelo próprio Qwen, marca 50,3 em BFCL-v4, 22,0 em VITA-Bench e 17,6 em DeepPlanning, e fica significativamente abaixo do 9B em várias tarefas agentic/coding. Validação de nome, schema, argumentos, permissões e resultados precisa estar fora do modelo. ([benchmarks oficiais](https://huggingface.co/Qwen/Qwen3.5-4B#benchmark-results); acesso em 2026-08-09.)
6. Não foi encontrada uma análise oficial específica que prove quais nomes de tools maximizam a acurácia do Qwen3.5-4B. A melhor evidência prática é a nomenclatura usada nos projetos oficiais Qwen-Agent e Qwen Code: nomes ASCII em `snake_case`, curtos, semanticamente distintos e orientados à ação, como `read_file`, `grep_search`, `run_shell_command`, `web_fetch`, `web_search`, `code_interpreter` e `image_search`. Isso é uma **recomendação baseada no corpus de integração oficial**, não um resultado causal publicado.

## 1. Identidade, variantes e licença

### 1.1 Checkpoint correto

| ID | Estágio | Uso adequado | Chat/tool calling |
|---|---|---|---|
| `Qwen/Qwen3.5-4B` | Pré-treino + pós-treino | Chat, agentes, texto, imagem e vídeo | Sim; template oficial incluído |
| `Qwen/Qwen3.5-4B-Base` | Apenas pré-treino | Fine-tuning, ICL experimental, pesquisa | Não é recomendado para interação direta |

**Fato documentado.** A página do Base afirma explicitamente que seus casos pretendidos são fine-tuning, experimentos de in-context learning e desenvolvimento, e não interação direta. Ela também informa que os tokens de controle `<|im_start|>` e `<|im_end|>` foram treinados para facilitar PEFT/LoRA com o template oficial. ([Base model card](https://huggingface.co/Qwen/Qwen3.5-4B-Base); acesso em 2026-08-09.)

**Recomendação:** o harness deve rejeitar por padrão IDs terminados em `-Base` quando estiver no modo agentic. Se houver suporte ao Base, ele deve ser um perfil separado, sem promessas de obediência a instruções ou tool calling.

### 1.2 Família Qwen3.5

A coleção oficial inclui modelos pequenos densos de 0,8B, 2B, 4B e 9B e variantes maiores densas/MoE, entre elas 27B, 35B-A3B, 122B-A10B e 397B-A17B. A nomenclatura `A<n>B` indica parâmetros ativos em variantes MoE. O `4B` não tem experts em seu `config.json`; seu FFN é denso. ([coleção oficial](https://huggingface.co/collections/Qwen/qwen35), [configuração do 4B](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json); acesso em 2026-08-09.)

Há uma sutileza documental: a seção genérica “Qwen3.5 Highlights” do card fala em Gated Delta Networks combinadas com sparse Mixture-of-Experts, mas a visão detalhada do **4B** mostra FFNs densos e nenhuma configuração de experts. Portanto, para dimensionar e implementar o 4B, prevalecem a tabela específica e o `config.json`, não o texto promocional comum à família.

### 1.3 Licença

Os pesos são distribuídos sob **Apache License 2.0**. A licença permite uso, modificação e distribuição sob suas condições de atribuição/redistribuição e inclui exclusão de garantias. Isso não substitui análise jurídica do caso de uso, das entradas, dos dados e das saídas geradas. ([LICENSE oficial](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/LICENSE); acesso em 2026-08-09.)

## 2. Arquitetura e capacidades

### 2.1 Backbone de linguagem

**Fatos documentados:**

- 4B parâmetros declarados para o language model, dimensão oculta 2.560, 32 camadas e vocabulário/embedding de 248.320 posições.
- Layout híbrido: 8 grupos de `3 × (Gated DeltaNet → FFN)` seguidos de `1 × (Gated Attention → FFN)`, totalizando 24 camadas de atenção linear e 8 de atenção completa.
- DeltaNet: 32 heads de V, 16 heads de QK, dimensão 128.
- Atenção completa: 16 heads de Q, 4 heads de KV, dimensão 256, RoPE parcial de dimensão 64.
- FFN de dimensão intermediária 9.216; embeddings e saída empatados.
- Uma camada MTP treinada para previsão de múltiplos tokens.
- `dtype` de configuração BF16; alguns estados internos lineares estão declarados em FP32 (`mamba_ssm_dtype`).

Fontes: [model card](https://huggingface.co/Qwen/Qwen3.5-4B#model-overview) e [config.json](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json) (acesso em 2026-08-09).

**Implicação para o harness:** esta arquitetura é híbrida e stateful. Otimizações, cache, speculative decoding e paralelismo que funcionam para Transformers convencionais não devem ser assumidos como equivalentes. O backend precisa declarar suas capacidades reais para `qwen3_5`, MTP, KV/cache linear, multimodalidade e tool parser.

### 2.2 Encoder visual e modalidades

O checkpoint é uma arquitetura `Qwen3_5ForConditionalGeneration`, classificada como Causal Language Model com Vision Encoder. O encoder possui 24 blocos, hidden size 1.024, 16 heads, patch 16, merge espacial 2 e patch temporal 2. O card apresenta chamadas para **texto, imagem e vídeo**; Qwen-Agent usa o tipo `qwenvl_oai`, definido como Text/Image/Video → Text. ([config](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json), [exemplos de API](https://huggingface.co/Qwen/Qwen3.5-4B#using-qwen35-via-the-chat-completions-api), [configuração Qwen-Agent](https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/configuration/); acesso em 2026-08-09.)

O tokenizer contém tokens relacionados a áudio/TTS, mas isso **não documenta suporte de entrada de áudio neste checkpoint**. O artefato tem encoder visual, não encoder de áudio, e os exemplos oficiais cobrem texto/imagem/vídeo. Tratar áudio como não suportado até existir teste e documentação específicos.

### 2.3 Idiomas e desempenho publicado

O Qwen declara suporte ampliado a 201 idiomas e dialetos. No card do 4B, os resultados incluem 79,1 em MMLU-Pro, 89,8 em IFEval, 55,8 em LiveCodeBench v6, 50,0 em LongBench v2 e 50,3 em BFCL-v4. Em visão, publica 77,6 em MMMU, 86,2 em OmniDocBench 1.5, 85,0 em OCRBench, 83,5 em VideoMME com legendas e 38,9/29,9 em TIR-Bench. ([resultados oficiais](https://huggingface.co/Qwen/Qwen3.5-4B#benchmark-results); acesso em 2026-08-09.)

Esses números são úteis como sinal comparativo, mas não garantem desempenho no harness porque:

- prompts, amostragem, parser e limite de saída afetam o resultado;
- muitos benchmarks são agregados e não cobrem schemas/tools particulares;
- tool calling executável exige seleção correta, argumentos válidos, resultado interpretado e ausência de ação indevida, não apenas formatação;
- o 4B perde para o 9B em várias tarefas de coding, planejamento e agentes, sugerindo maior sensibilidade a ambiguidade e contexto ruidoso.

## 3. Contrato de chat e reasoning

### 3.1 Template não é opcional

O template Jinja oficial implementa um formato ChatML-like com `<|im_start|>{role}` e `<|im_end|>`. O caminho recomendado em Transformers é `AutoProcessor.apply_chat_template(..., add_generation_prompt=True, tokenize=True)`. Para multimodal, o processor insere placeholders `<|vision_start|><|image_pad|><|vision_end|>` ou `<|vision_start|><|video_pad|><|vision_end|>` e prepara os tensores de mídia. ([quickstart](https://huggingface.co/Qwen/Qwen3.5-4B), [tokenizer_config.json](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json); acesso em 2026-08-09.)

Contratos diretamente codificados no template:

- a mensagem `system`, se existir, precisa ser a primeira;
- conteúdo de `system` não pode conter imagem ou vídeo;
- roles aceitos pelo template são `system`, `user`, `assistant` e `tool`; qualquer outro gera erro;
- tool results consecutivos são agrupados numa mensagem wire de papel `user`, dentro de `<tool_response>`;
- o prompt de geração termina em `<|im_start|>assistant\n<think>\n` no modo padrão;
- com `enable_thinking=false`, termina em um bloco vazio `<think>\n\n</think>\n\n`.

**Recomendação:** conservar uma representação canônica independente do backend e renderizar sempre com o processor/template do mesmo revision dos pesos. Nunca concatenar manualmente “User:”/“Assistant:” nem copiar o template de outra versão Qwen.

### 3.2 Thinking mode

O modelo pensa por padrão. O Qwen recomenda preservar apenas a resposta final no histórico de conversas multi-turno; o conteúdo de thinking histórico não deve ser reinjetado. O template oficial já implementa essa política, mas adaptadores que não usam o Jinja precisam replicá-la. ([best practices](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices); acesso em 2026-08-09.)

Consequências para o harness:

- separar `reasoning_content` de `content` na resposta interna;
- não exibir, persistir nem logar reasoning por padrão; pode conter dados sensíveis ou instruções intermediárias;
- não executar uma tool detectada dentro de texto de reasoning sem o parser ter emitido um evento estrutural de tool call;
- tratar `<tool_call>` como possível fim implícito do reasoning, comportamento codificado no parser atual do vLLM;
- limitar tempo/tokens de reasoning e registrar término por `stop`, comprimento, cancelamento ou erro;
- comparar `enable_thinking=true` e `false` em avaliação. Sem pensar simplifica protocolo e latência; com pensar pode melhorar tarefas difíceis, mas aumenta superfície de parsing e custo.

Parâmetros recomendados pelo Qwen:

| Perfil | `temperature` | `top_p` | `top_k` | `presence_penalty` |
|---|---:|---:|---:|---:|
| Thinking, geral | 1,0 | 0,95 | 20 | 1,5 |
| Thinking, coding preciso | 0,6 | 0,95 | 20 | 0,0 |
| Sem thinking, geral | 0,7 | 0,8 | 20 | 1,5 |
| Sem thinking, reasoning | 1,0 | 1,0 | 40 | 2,0 |

Esta tabela segue deliberadamente a seção **Best Practices**. O próprio model card contém uma inconsistência: na seção anterior de API, o perfil “non-thinking mode for reasoning” aparece como `temperature=1.0`, `top_p=0.95`, `top_k=20` e `presence_penalty=1.5`, enquanto Best Practices recomenda `1.0/1.0/40/2.0`. Portanto, o harness não deve tratar esses valores como um default universal silencioso: salvar um perfil de sampling nomeado junto com revision de modelo/template e comparar os dois perfis no conjunto de avaliação. Até a divergência ser resolvida pelo upstream, este documento usa Best Practices como referência principal. ([seção de API](https://huggingface.co/Qwen/Qwen3.5-4B#using-qwen35-via-the-chat-completions-api), [Best Practices](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices); acesso em 2026-08-09.)

O card alerta que penalidade alta pode causar mistura de idiomas e pequena perda de desempenho, e que nem todo runtime suporta todos os parâmetros. ([best practices](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices); acesso em 2026-08-09.)

## 4. Tool/function calling

### 4.1 Formato wire oficial

Quando `tools` é fornecido ao template, cada definição é serializada em JSON dentro de `<tools>` na mensagem de sistema. O template instrui o modelo a emitir, sem sufixo:

```text
<tool_call>
<function=nome_da_funcao>
<parameter=nome_do_parametro>
valor
</parameter>
</function>
</tool_call>
```

Pode haver linguagem natural/reasoning **antes** da chamada, nunca depois. Múltiplos blocos `<tool_call>` representam chamadas paralelas. Resultados voltam como blocos `<tool_response>...</tool_response>`. ([template oficial completo](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json); acesso em 2026-08-09.)

O vLLM atual implementa uma máquina de estados conjunta para `<think>` e tools. Ela tolera alguns casos malformados, converte os `<parameter>` para objeto JSON e suporta deltas de argumentos no streaming. Isso reforça que o parser é parte do protocolo, não uma regex periférica. ([parser Qwen3 do vLLM](https://github.com/vllm-project/vllm/blob/main/vllm/parser/qwen3.py); acesso em 2026-08-09.)

### 4.2 Representação canônica recomendada

Internamente, usar uma estrutura semelhante a:

```json
{
  "id": "call_01...",
  "name": "read_file",
  "arguments": {"file_path": "/workspace/README.md"},
  "raw_arguments": "...",
  "source": "structured_parser",
  "index": 0
}
```

Regras:

1. `arguments` deve ser um **objeto**, validado contra o schema registrado.
2. `raw_arguments` preserva evidência para diagnóstico, mas nunca é executado diretamente.
3. IDs podem ser criados pelo servidor/harness. O template raw não preserva `tool_call_id`; a associação dos resultados é, na prática, posicional.
4. Uma chamada só é executável depois de o parser emitir fechamento estrutural, nome cadastrado e argumentos completos/válidos.
5. Em chamadas paralelas, preservar a ordem original e correlacionar cada resultado pelo ID interno.
6. Rejeitar nomes desconhecidos, argumentos extras quando o schema proibir, tipos inválidos e payloads acima do limite.

### 4.3 Armadilhas de serialização

O template Qwen3.5 itera `tool_call.arguments|items`; portanto, ao renderizar **histórico** com `apply_chat_template`, `arguments` precisa ser dict/objeto, não uma string contendo JSON. Uma análise sistemática no repositório Transformers confirmou que Qwen3.5 aceita dict e falha com string; `content=""` é a forma mais interoperável para mensagens assistant com `tool_calls`. ([issue/análise oficial do Transformers](https://github.com/huggingface/transformers/issues/45419); acesso em 2026-08-09.)

Isso cria uma fronteira importante:

- APIs OpenAI-compatible frequentemente retornam `function.arguments` como **string JSON**;
- o histórico do template Qwen3.5 espera `arguments` como **objeto**.

O adaptador deve fazer `JSON.parse` + validação na entrada e reter objeto ao renderizar o próximo turno. Não repassar cegamente a resposta OpenAI para `apply_chat_template`.

Há ainda colisão possível com os delimitadores do protocolo: strings de argumento são inseridas sem XML escaping entre `<parameter=...>` e `</parameter>`. Conteúdo que inclua literalmente `</parameter>` ou uma nova abertura `<parameter=...>` pode ser confundido com estrutura pelo parser. **Inferência a testar:** evitar transportar blobs/código arbitrário grande diretamente em parâmetros; preferir referências a artefatos, patches tipados ou operações por posição, e executar testes diferenciais com todos os sentinels do protocolo.

### 4.4 Tool results

O template não usa `tool_call_id`, `name` nem metadata da mensagem `tool` ao renderizar o wire; ele coloca apenas o conteúdo em `<tool_response>`. Isso permite multi-step e blocos consecutivos, mas perde correlação explícita no prompt.

O conteúdo do resultado também é inserido sem escaping. Um arquivo ou página contendo `</tool_response>`, `<tool_call>` ou `<think>` pode colidir com o protocolo e ampliar prompt injection. Para resultados grandes/adversariais, armazenar o original fora do prompt e devolver resumo/chunks limitados com referência e escaping reversível; nunca perder o original usado para auditoria.

**Inferência/recomendação:** para paralelismo seguro, o harness deve:

- aguardar todas as chamadas do lote ou definir uma ordem estável;
- devolver resultados na mesma ordem das chamadas;
- incluir no conteúdo um envelope compacto como `{"ok":true,"data":...}`;
- evitar repetir o nome/ID no texto se isso só consumir tokens, mas habilitar um envelope com ID se os testes mostrarem confusão;
- não misturar respostas de turnos/sessões concorrentes.

## 5. Backends e versionamento

### 5.1 Matriz de integração

| Backend | Papel recomendado | Configuração relevante | Riscos/observações |
|---|---|---|---|
| Transformers | Referência de template e testes; carga leve | versão recente/main, `AutoProcessor`, `AutoModelForMultimodalLM` | servidor é descrito como adequado a testes e carga moderada; precisa `torchvision` e Pillow |
| vLLM | produção/alto throughput | `--reasoning-parser qwen3 --enable-auto-tool-choice --tool-call-parser qwen3_coder` | pin de versão; validar streaming, cache híbrido, MTP e formatos OpenAI |
| SGLang | produção/alto throughput | `--reasoning-parser qwen3 --tool-call-parser qwen3_coder` | card pede main; pin e testes de contrato |
| KTransformers | CPU–GPU heterogêneo | guia próprio | mais uma combinação de kernels/cache a validar |
| llama.cpp/GGUF | dispositivos locais/quantizados | suporte `qwen35`, conversor e runtime da mesma revisão | quantizações geralmente comunitárias; regressões recentes de conversão/load exigem smoke tests |
| Qwen-Agent | agente de referência/adaptador | `qwenvl_oai`, `use_raw_api`, MCP/tools | útil para comparar comportamento; não elimina necessidade de sandbox e validação |

Fontes: [serving no model card](https://huggingface.co/Qwen/Qwen3.5-4B#serving-qwen35), [Qwen-Agent](https://github.com/QwenLM/Qwen-Agent), [receita vLLM](https://github.com/vllm-project/recipes/blob/main/Qwen/Qwen3.5.md), [conversor llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/convert_hf_to_gguf.py) (acesso em 2026-08-09).

### 5.2 Comandos-base oficiais

vLLM com tools:

```bash
vllm serve Qwen/Qwen3.5-4B \
  --port 8000 \
  --tensor-parallel-size 1 \
  --max-model-len 262144 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder
```

SGLang com tools:

```bash
python -m sglang.launch_server \
  --model-path Qwen/Qwen3.5-4B \
  --port 8000 \
  --tp-size 1 \
  --mem-fraction-static 0.8 \
  --context-length 262144 \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder
```

Estes são os comandos do card, não uma recomendação de começar em 262K. Em ambiente real, definir `--max-model-len` pelo orçamento de memória e pelo corpus de testes.

Para texto puro, vLLM oferece `--language-model-only`, que pula encoder visual e profiling multimodal para liberar memória ao cache. Para MTP, o card recomenda `--speculative-config '{"method":"qwen3_next_mtp","num_speculative_tokens":2}'`. SGLang tem sua configuração `NEXTN`. Tratar MTP como otimização opcional: medir latência **e** throughput, pois speculative tokens consomem cache e podem reduzir batch sob concorrência. ([model card](https://huggingface.co/Qwen/Qwen3.5-4B#serving-qwen35), [receita vLLM](https://github.com/vllm-project/recipes/blob/main/Qwen/Qwen3.5.md); acesso em 2026-08-09.)

### 5.3 Política de versões

O card foi publicado com dependência explícita de branches main/nightly para SGLang, vLLM e Transformers; o `config.json` registra `transformers_version: 4.57.0.dev0`. Mesmo que releases estáveis atuais já incluam suporte, o harness deve registrar:

- revision/hash do modelo e tokenizer;
- digest da imagem/versão do runtime;
- versão de CUDA/ROCm, PyTorch e drivers;
- parser de reasoning e parser de tools;
- chat template hash;
- quantização e origem do arquivo;
- flags completas de inicialização.

Toda atualização passa por golden tests de template, parser, streaming e execução antes de produção.

**Gate de segurança obrigatório para vLLM:** não implantar a faixa `>=0.10.0,<0.10.1.1` com `--enable-auto-tool-choice` e o parser `qwen3_coder`. O advisory oficial GHSA-79j6-g2m3-jgfw/CVE-2025-9141 descreve execução remota de código por desserialização insegura de tipos de parâmetro desconhecidos; a versão corrigida é `0.10.1.1`. Um usuário autenticado que induzisse o modelo a passar código como argumento podia atingir o caminho vulnerável. Fixar uma versão posterior, verificar o advisory no scanner de dependências e ainda manter execução de tools isolada. ([advisory do vLLM](https://github.com/vllm-project/vllm/security/advisories/GHSA-79j6-g2m3-jgfw); acesso em 2026-08-09.)

### 5.4 llama.cpp e quantização

O llama.cpp atual possui arquitetura `qwen35` e o conversor reconhece variantes Qwen3.5/3.6 e MTP. Porém, issues recentes no próprio upstream relatam incompatibilidades em conversões intermediárias e um caso específico de fine-tune Qwen3.5-4B cujo GGUF passa a esperar um bloco inexistente. Isso não prova falha do checkpoint oficial convertido pela revisão atual, mas prova que “converteu sem erro” não é verificação suficiente. ([conversor](https://github.com/ggml-org/llama.cpp/blob/master/convert_hf_to_gguf.py), [issue de conversão MLX](https://github.com/ggml-org/llama.cpp/issues/23758), [issue 4B/32 blocos](https://github.com/ggml-org/llama.cpp/issues/24737); acesso em 2026-08-09.)

Para cada GGUF/quant:

1. registrar origem e checksum;
2. validar contagem de 32 camadas e token IDs;
3. rodar geração conhecida em texto;
4. testar template, thinking on/off, tool call e multi-turn;
5. testar multimodal somente se projector/runtime forem explicitamente suportados;
6. comparar qualidade e taxa de tool success contra BF16/FP16 de referência.

Não foi encontrado um GGUF oficial `Qwen/Qwen3.5-4B-GGUF`; as quantizações listadas no Hub são majoritariamente de terceiros. Isso é uma lacuna de supply chain e reprodutibilidade.

## 6. Contexto, memória e capacidade de runtime

### 6.1 Pesos

O índice oficial informa `total_size = 9.319.737.856` bytes, cerca de **8,68 GiB** em disco para os safetensors. Esse total inclui language model, visão e MTP. ([índice oficial](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/model.safetensors.index.json); acesso em 2026-08-09.)

Estimativas apenas para planejamento, antes de overhead/ativações:

| Representação | Ordem de grandeza dos pesos | Observação |
|---|---:|---|
| BF16 original | 8,68 GiB | valor do artefato oficial; runtime pode alocar buffers adicionais |
| 8-bit | ~4,3–5,2 GiB | depende de escala, metadata e tensores mantidos em maior precisão |
| 4-bit | ~2,2–3,5 GiB | depende muito do formato; encoder/MTP podem não usar o mesmo bit-width |

**Inferência:** as faixas quantizadas são aproximações, não requisitos oficiais. Não prometer que uma GPU de tamanho igual ao arquivo executará o modelo: somam-se cache, ativações, workspace de kernels, processor visual e concorrência.

### 6.2 KV cache estimado

Nas oito camadas de atenção completa:

```text
elementos KV/token = 2 (K,V) × 4 KV heads × 256 head_dim × 8 camadas
                   = 16.384 elementos/token
BF16               = 32.768 bytes/token = 32 KiB/token
FP8 KV              = 16.384 bytes/token = 16 KiB/token
```

Estimativa por sequência:

| Tokens totais | KV BF16 | KV FP8 |
|---:|---:|---:|
| 32.768 | ~1 GiB | ~0,5 GiB |
| 65.536 | ~2 GiB | ~1 GiB |
| 131.072 | ~4 GiB | ~2 GiB |
| 262.144 | ~8 GiB | ~4 GiB |
| 1.010.000 | ~30,8 GiB | ~15,4 GiB |

**Inferência técnica a validar.** A conta usa a configuração oficial e cobre o KV das camadas full-attention. Não inclui estados recorrentes/conv das 24 DeltaNet, padding/alinhamento do backend, MTP, ativações, visão, prefix cache nem fragmentação. O layout real pode diferir.

O card aconselha manter ao menos 128K para preservar thinking em tarefas complexas, mas também manda reduzir a janela diante de OOM. Para um harness geral, a decisão deve ser orientada por dados: 32K/64K para baseline, 128K para tarefas que comprovadamente se beneficiem, e 262K como perfil explícito de long context. ([serving oficial](https://huggingface.co/Qwen/Qwen3.5-4B#serving-qwen35); acesso em 2026-08-09.)

### 6.3 Orçamento input + output

O limite de contexto inclui entrada e saída. O Qwen recomenda até 32.768 tokens de saída na maioria das consultas e 81.920 para problemas de competição complexos. Esses valores são tetos de qualidade, não defaults econômicos para ferramentas. Reservar dinamicamente saída e cortar/sumarizar o input antes de exceder o limite. ([best practices](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices); acesso em 2026-08-09.)

Para agentes, a política do Qwen-Agent é remover turnos antigos, comprimir tool responses, remover etapas antigas de tools e só então truncar query/resposta recente. Esse desenho é uma boa referência para manter unidades semânticas, em vez de truncar tokens no meio de um tool call. ([gestão de contexto Qwen-Agent](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/context/); acesso em 2026-08-09.)

### 6.4 YaRN e 1M

O contexto nativo é 262.144. O Qwen documenta extensão até 1.010.000 via YaRN com `factor: 4.0` e `original_max_position_embeddings: 262144`. Implementações open source usam YaRN estático; o próprio card alerta que isso pode prejudicar textos curtos e recomenda habilitá-lo somente quando necessário, ajustando o fator ao comprimento típico. ([long context](https://huggingface.co/Qwen/Qwen3.5-4B#processing-ultra-long-texts); acesso em 2026-08-09.)

Logo, manter perfis separados:

- `native-short`: sem YaRN, contexto operacional reduzido;
- `native-long`: sem YaRN, até 262K;
- `yarn-extended`: configuração e workers dedicados, sem misturar tráfego curto.

### 6.5 Multimodalidade compete pelo mesmo orçamento

Imagens e frames viram tokens visuais. No exemplo de vídeo, vLLM usa `fps=2` por padrão, configurável por `mm_processor_kwargs`; o card afirma que essa configuração de sampling é específica do vLLM. Para vídeo longo, o Qwen sugere elevar `longest_edge` até um valor correspondente a 224K tokens visuais, o que praticamente consome a janela nativa. ([vídeo e best practices](https://huggingface.co/Qwen/Qwen3.5-4B#using-qwen35-via-the-chat-completions-api); acesso em 2026-08-09.)

O harness deve impor limites de bytes, dimensões, frames, duração, FPS e tokens estimados antes de baixar/decodificar mídia.

## 7. Complicações específicas do harness

### 7.1 Pipeline recomendado

```text
mensagem canônica
  → validação de roles/mídia/tools
  → adaptador de chat template por revision
  → backend de inferência fixado
  → parser incremental reasoning/content/tool
  → normalizador OpenAI ↔ objeto interno
  → validação JSON Schema + política
  → aprovação/sandbox/executor
  → envelope de resultado limitado
  → histórico sem thinking antigo
```

Cada seta é uma fronteira testável. O modelo nunca chama código diretamente.

### 7.2 Streaming

No streaming, tags e valores podem ser divididos em qualquer byte/token. O parser deve:

- manter estado por request, nunca global;
- aceitar tags fragmentadas entre chunks;
- só expor uma chamada completa após fechamento ou evento estrutural equivalente;
- acumular `arguments` por índice/ID;
- separar deltas de reasoning, content e tool arguments;
- suportar cancelamento sem executar chamada parcial;
- limitar buffer quando a saída é malformada ou nunca fecha tags;
- guardar a saída raw para diagnóstico com redaction.

**Evidência de upstream:** houve relatos no vLLM de tools Qwen3.5 chegando como XML em `content` no streaming/Responses API e de conflito entre reasoning e tool parser. Um issue específico de Qwen3.5-27B com thinking não prova que o 4B ou Chat Completions atuais falhem; serve como caso de regressão obrigatório. ([issue de streaming](https://github.com/vllm-project/vllm/issues/31871), [issue reasoning + tool](https://github.com/vllm-project/vllm/issues/42021); acesso em 2026-08-09.)

Outro issue mostrou que vLLM 0.24/0.25.1 removia whitespace significativo de parâmetros como `old_string`. O `main` acessado em 2026-08-09 já contém `_trim_wrapping_newlines`, que remove apenas os delimitadores de newline do wire. Assim, versões antigas podem corromper edições de código; a correção em `main` não deve ser presumida numa wheel sem verificar. ([issue](https://github.com/vllm-project/vllm/issues/48753), [código atual](https://github.com/vllm-project/vllm/blob/main/vllm/parser/qwen3.py); acesso em 2026-08-09.)

### 7.3 Concorrência

Há três níveis distintos:

1. **batch de inferência:** múltiplas sessões compartilham GPU, mas não estado de parser/histórico;
2. **parallel tool calls:** uma resposta pede várias tools;
3. **execução paralela:** o harness decide se pode executá-las simultaneamente.

Não executar em paralelo operações com dependência ou side effects conflitantes. Adotar locks por recurso, idempotency keys, timeout, cancelamento e limite de fan-out. A ausência de `tool_call_id` no wire torna a ordem uma parte do contrato.

Sob alta concorrência, MTP pode reduzir latência por token e ainda piorar throughput por consumir cache. O vLLM também documenta prefix caching de cache Mamba/linear como experimental em sua receita. Benchmarkar P50/P95/P99, TTFT, TPOT, throughput e OOM com o mix real. ([receita vLLM](https://github.com/vllm-project/recipes/blob/main/Qwen/Qwen3.5.md); acesso em 2026-08-09.)

### 7.4 Schemas

**Fato:** Qwen-Agent modela tools com nome, descrição e JSON Schema de objeto; o exemplo oficial usa `properties`, `required`, tipos e descrições. ([Tool Introduction](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/tool/); acesso em 2026-08-09.)

**Recomendações a validar empiricamente para o 4B:**

- manter poucos campos e pouca profundidade;
- marcar somente os realmente necessários como `required`;
- usar tipos concretos; evitar `anyOf`/`oneOf`, unions ambíguas e objetos livres;
- usar `enum` para conjuntos fechados;
- colocar unidade no nome/descrição (`timeout_ms`, `temperature_celsius`);
- separar leitura de escrita e preview de apply;
- negar propriedades desconhecidas no validador, independentemente de `additionalProperties` no prompt;
- validar comprimento, regex segura, ranges, paths e URLs após parse;
- devolver erros estruturados curtos e acionáveis ao modelo;
- não aceitar comandos shell quando uma tool tipada resolver a operação.

### 7.5 Tokens especiais e injeção de protocolo

Os IDs relevantes incluem `<|endoftext|>` 248044, `<|im_start|>` 248045, `<|im_end|>` 248046, `<|vision_start|>` 248053, `<|vision_end|>` 248054, `<|image_pad|>` 248056 e `<|video_pad|>` 248057. As strings `<tool_call>`, `</tool_call>`, `<tool_response>`, `</tool_response>`, `<think>` e `</think>` também têm entradas no vocabulário, mas várias não são marcadas como `special` na lista `additional_special_tokens`. ([tokenizer_config](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json); acesso em 2026-08-09.)

Não usar simples busca de substring em texto do usuário ou tool result para detectar chamadas. Conteúdo não confiável pode incluir as mesmas tags. A detecção precisa operar apenas na saída do assistant dentro do estado correto do parser. Resultados de tool devem ser considerados **dados não confiáveis**, nunca novas instruções de sistema.

## 8. Nomes de tools mais amigáveis ao Qwen

### 8.1 O que a evidência permite afirmar

Não foi localizada uma ablação oficial “nome A versus nome B” para Qwen3.5-4B. A evidência primária disponível é a nomenclatura escolhida pelos próprios projetos Qwen:

- Qwen-Agent: `code_interpreter`, `web_search`, `web_extractor`, `image_search`, `image_zoom_in_tool` e, em exemplos de gerações diferentes, `my_image_gen` e `image_gen`; ([features](https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/features/), [custom tool](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/tool/), [exemplo Qwen3-Coder com `image_gen`](https://github.com/QwenLM/Qwen-Agent/blob/main/examples/assistant_qwen3_coder.py); acesso em 2026-08-09.)
- Qwen Code: `read_file`, `write_file`, `edit`, `glob`, `grep_search`, `list_directory`, `run_shell_command`, `web_fetch`, `todo_write`, `agent` e `ask_user_question`; ([tools](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/introduction/), [aliases/canônicos](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md), [SDK e `ask_user_question`](https://github.com/QwenLM/qwen-code/blob/main/packages/sdk-typescript/README.md); acesso em 2026-08-09.)
- Exemplo de schema Qwen-Agent: `get_weather` com argumento `city`; exemplos MCP usam aliases `time`, `fetch`, `filesystem`. ([schema](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/schema/), [configuração](https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/configuration/); acesso em 2026-08-09.)

O template demonstra `example_function_name` e insere o nome diretamente em `<function=NAME>`. Isso favorece nomes simples sem espaços ou caracteres com função sintática em XML.

### 8.2 Convenção recomendada

1. **ASCII minúsculo em `snake_case`.** Evitar espaços, acentos, hífen, ponto, dois-pontos, barras e `<>&=`.
2. **Verbo + objeto para ações:** `read_file`, `web_search`, `create_issue`. Nomes nominais são aceitáveis apenas quando já canônicos, como `code_interpreter`.
3. **Curto, mas inequívoco:** `get_weather` é melhor que `get`; `run_shell_command` é melhor que `execute`.
4. **Ferramentas mutuamente distintas:** não expor `search`, `find`, `lookup` e `query` com descrições sobrepostas.
5. **Nome estável; versão fora do nome.** Não criar `read_file_v2`; versionar schema no registry/metadata.
6. **Mesmo vocabulário nos parâmetros:** `file_path` para arquivo conhecido; `path` para raiz de busca quando esse for o contrato oficial; além de `query`, `url`, `pattern`, `content` e `timeout_ms`.
7. **Descrição começa pela capacidade e explicita limites/side effects.** O nome ajuda a seleção; a descrição e o schema decidem o uso correto.

### 8.3 Catálogo oficial de referência, não catálogo local

Esta tabela registra nomes encontrados no ecossistema oficial para comparação.
Ela inclui capacidades fora do baseline do Harness 2.0, como shell e
interpretador. Shell está adiada, não proibida permanentemente: só pode entrar
com sandbox, aprovação, auditoria e gates próprios. O catálogo local aprovado
contém exatamente oito tools e está em
[`config/tool-registry.json`](../../config/tool-registry.json). Priorizar nomes
oficiais apenas quando a semântica e a política local coincidirem:

| Capacidade | Nome recomendado | Parâmetros centrais | Motivo |
|---|---|---|---|
| Ler arquivo | `read_file` | `file_path`, `offset`, `limit` | nome e campo canônicos no Qwen Code |
| Gravar arquivo novo/inteiro | `write_file` | `file_path`, `content` | canônico; side effect evidente |
| Editar trecho | `edit` | `file_path`, `old_string`, `new_string`, `replace_all` | nome e campos canônicos atuais do Qwen Code; testar `edit_file` apenas como variante A/B, nunca como alias simultâneo |
| Listar diretório | `list_directory` | `path`, `depth` | canônico e inequívoco |
| Encontrar por glob | `glob` | `pattern`, `path` | canônico; descrição deve distinguir de busca textual |
| Buscar texto | `grep_search` | `pattern`, `path`, `include` | canônico e distinto de `web_search` |
| Rodar shell | `run_shell_command` | `command`, `timeout_ms` | canônico; deve exigir política/sandbox |
| Buscar web | `web_search` | `query`, `max_results` | canônico no Qwen-Agent |
| Obter URL | `web_fetch` | `url`, `prompt` | canônico no Qwen Code |
| Extrair página | `web_extractor` | `url`, `query` | canônico no Qwen-Agent; só expor se distinto de fetch |
| Interpretar Python | `code_interpreter` | `code`, `timeout_ms` | canônico no Qwen-Agent |
| Clima | `get_weather` | `location`, `unit` | padrão de exemplo oficial |
| Hora | `get_current_time` | `timezone` | mais claro que o alias MCP genérico `time` |
| Buscar imagem | `image_search` | `query` | canônico no Qwen-Agent |
| Zoom/crop | `zoom_image` | `image`, `bounding_box` | baseline curto e explícito; `image_zoom_in_tool`/`zoom_image_region` ficam somente como variantes A/B |
| Gerar imagem | `image_gen` | `prompt` | nome usado no exemplo oficial recente; `generate_image` fica somente como variante A/B |
| Tarefas | `todo_write` | `items` | canônico no Qwen Code |
| Perguntar ao usuário | `ask_user_question` | `questions` | nome e entrada canônicos atuais no SDK do Qwen Code; exige suporte interativo do produto |

**Não expor simultaneamente as alternativas da mesma linha.** A tabela apresenta decisões onde vale A/B test, não aliases para o modelo.

Evitar:

| Nome ruim | Problema | Melhor |
|---|---|---|
| `tool1`, `do`, `execute` | sem semântica | verbo + objeto |
| `readOrWriteFile` | mistura permissões e intenção | `read_file` + `write_file` |
| `filesystem` | namespace genérico, não ação | operações tipadas |
| `mcp__server__read-file` | ruído de transporte no nome exposto | alias canônico `read_file` |
| `obter_previsão_do_tempo_agora` | longo, idioma/acentos futuros | `get_weather` com descrição em português |
| `search` | ambíguo entre arquivos/web/dados | `grep_search`, `web_search`, `search_records` |
| `bash` | sugere poder irrestrito e não descreve contrato | `run_shell_command`, com sandbox |

### 8.4 Como escolher de verdade

Criar um eval de seleção com pares confundíveis. Para cada candidato de nome, medir:

- acurácia de escolher a tool correta;
- taxa de chamada desnecessária;
- JSON/schema válido;
- argumentos exatos;
- sucesso após execução;
- confusão entre tools vizinhas;
- tokens de prompt e latência.

Testar nome **junto** de descrição e schema; trocar só o nome enquanto o restante fica fixo. Sem isso, “melhor nome” é opinião.

## 9. Limitações e riscos

### 9.1 Limitações documentadas ou diretamente observáveis

- Contexto nativo limitado a 262.144; 1.010.000 depende de YaRN e pode prejudicar contexto curto.
- `/think` e `/nothink` não são suportados como soft switch.
- Thinking deve ser removido do histórico antigo.
- Maior `presence_penalty` pode causar mistura de idiomas e queda de qualidade.
- Suporte a parâmetros de sampling varia por runtime.
- Ferramentas e reasoning dependem de parsers/configuração explícitos no serving.
- O checkpoint completo carrega encoder visual mesmo em workloads texto, salvo otimização como `--language-model-only`.
- O card não apresenta uma seção detalhada de riscos, vieses, usos proibidos ou avaliação de segurança específica do 4B.

### 9.2 Limitações inferidas para um 4B agentic

- Maior chance de selecionar a tool errada quando há muitas opções semelhantes.
- Menor robustez a schemas grandes/profundamente aninhados e descrições longas.
- Planejamento longo e recuperação de erro provavelmente são mais frágeis que no 9B/27B, coerente com os benchmarks comparativos.
- A janela nominal não garante recuperação fiel em todo o comprimento; LongBench v2 = 50,0 e AA-LCR = 57,0 mostram que long-context capability não é perfeição.
- Quantização pode afetar de forma desproporcional tokens estruturais, argumentos exatos, raciocínio e visão; precisa de eval específico.
- Reasoning mais longo não garante tool call melhor e pode aumentar a probabilidade de tags malformadas/latência.

### 9.3 Lacunas

- Não foi encontrada system card/safety card oficial específica do `Qwen3.5-4B`.
- Não há requisitos oficiais mínimos de VRAM/RAM por contexto e concorrência para o 4B.
- Não há matriz oficial completa de versões estáveis vLLM/SGLang/Transformers/llama.cpp certificadas em conjunto.
- Não há estudo oficial de nomes de tools ou complexidade máxima confiável de JSON Schema para o 4B.
- Não foi encontrada garantia oficial de tool calling em todas as quantizações/GGUF.
- Não há taxa oficial de sucesso para português + tools no domínio deste projeto.
- Os benchmarks do card não fornecem no próprio card todos os prompts, seeds e detalhes de cada pipeline; reproduzir antes de usar como SLA.
- Issues de streaming/reasoning citados abrangem versões/endpoints/modelos específicos e precisam de reprodução no 4B escolhido.

## 10. Segurança operacional

Qwen Code usa confirmação para ações sensíveis e sandbox para shell/modificação de arquivos; sua documentação enfatiza que sandbox reduz, mas não elimina riscos. Qwen-Agent também alerta que executores Python não isolados são apenas para teste e oferece code interpreter baseado em contêiner. ([segurança de tools](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/introduction/), [sandbox](https://qwenlm.github.io/qwen-code-docs/en/users/features/sandbox/), [Qwen-Agent](https://github.com/QwenLM/Qwen-Agent); acesso em 2026-08-09.)

Controles mínimos do harness:

1. **Deny by default:** registry allowlisted; nome gerado não cadastrado nunca executa.
2. **Privilégio mínimo:** separar tools read-only, mutação e externas; workers e credenciais distintos.
3. **Aprovação:** confirmação humana ou policy engine para deleção, escrita fora do workspace, publicação, pagamentos, mensagens, mudanças remotas e shell arriscado.
4. **Sandbox:** filesystem limitado, usuário sem privilégio, rede deny/allowlist, limites de CPU/RAM/processos/tempo, filesystem efêmero quando possível.
5. **Validação:** JSON Schema + validação semântica de path/URL/range; canonicalizar paths e bloquear traversal/symlink escape.
6. **Rede:** bloquear SSRF, IPs privados/metadata cloud, redirects não permitidos, esquemas não HTTP(S), DNS rebinding e downloads grandes.
7. **Segredos:** nunca colocar secrets em prompt/tool description; injetar no executor e redigir logs/resultados.
8. **Resultados não confiáveis:** páginas, arquivos e MCP podem conter prompt injection. Delimitar, rotular como dados e nunca promover a system.
9. **Limites:** quantidade de calls/turno, paralelismo, retries, bytes de input/output, duração e custo.
10. **Idempotência/auditoria:** IDs, actor/session, schema version, argumentos redigidos, decisão de policy, resultado, timestamps e checksum.
11. **Retries conscientes:** não repetir automaticamente mutações sem idempotency key.
12. **Kill switch:** cancelar geração, tool e subtarefas; encerrar processos descendentes.

Além do sandbox do executor, o próprio parser é superfície crítica: bloquear explicitamente vLLM `>=0.10.0,<0.10.1.1`, faixa vulnerável à RCE do `qwen3_coder`, e exigir scanner/SBOM no gate de release. Atualizar o runtime não substitui validação de schema, allowlist e isolamento do processo da tool. ([GHSA-79j6-g2m3-jgfw](https://github.com/vllm-project/vllm/security/advisories/GHSA-79j6-g2m3-jgfw); acesso em 2026-08-09.)

Qwen Code adicionou um approval gate para MCP de projeto após reconhecer que um repositório malicioso poderia iniciar servidor configurado localmente. O harness deve exigir aprovação/hash de configuração para MCPs e revogar quando comando, args, URL, env ou headers mudarem. ([nota oficial do Qwen Code](https://qwenlm.github.io/qwen-code-docs/en/blog/updates/weekly-update-2026-06-18/); acesso em 2026-08-09.)

## 11. Plano de testes do harness

### 11.1 Contrato de template

- snapshot do prompt renderizado com/sem system;
- thinking true/false;
- texto, imagem, múltiplas imagens e vídeo;
- rejeição de mídia em system;
- tool definitions no system;
- assistant `tool_calls.arguments` como objeto;
- `content`: `""`, `null` e ausente, com normalização;
- uma, várias e zero tool calls;
- vários tool results consecutivos;
- histórico sem reasoning antigo;
- token IDs e stops corretos.

### 11.2 Parser e streaming

- chunk em cada fronteira possível das tags;
- Unicode, aspas, barras, `<`, `>`, `&`, tabs e newlines em valores;
- ocorrências literais de `<parameter=`, `</parameter>`, `<tool_call>`, `</tool_response>` e `<think>` em argumentos/resultados;
- strings com indentação/trailing newline para prevenir regressão de whitespace;
- argumentos objetos/arrays e strings que parecem JSON;
- tags incompletas, duplicadas, fora de ordem e sem fechamento;
- tool call que começa diretamente no reasoning;
- reasoning sem `</think>` antes da tool;
- texto que contém tags como dado, mas não é chamada;
- cancelamento em cada estado;
- limite de buffer e timeout;
- paridade streaming versus non-streaming.

### 11.3 Capacidade agentic

Construir um conjunto versionado em português e inglês com:

- tool claramente necessária;
- nenhuma tool necessária;
- duas tools parecidas;
- argumento ausente que exige pergunta ao usuário;
- multi-step sequencial;
- chamadas paralelas independentes;
- resultados vazios, grandes, malformados e de erro;
- recuperação após erro transitório e permanente;
- dados contendo prompt injection;
- side effect que exige aprovação;
- tentativa de path traversal/SSRF/comando proibido.

Métricas:

- seleção: precision/recall/F1 e unnecessary-call rate;
- argumentos: parse rate, schema-valid rate, exact match e semantic match;
- execução: task success, recovery success e side-effect violation rate;
- formato: raw-XML leak, malformed-call rate, stream/non-stream parity;
- desempenho: TTFT, TPOT, tokens de reasoning/final, P50/P95/P99, throughput, OOM;
- custo de contexto: tokens de definitions, histórico e tool results.

### 11.4 Matriz mínima

| Eixo | Valores mínimos |
|---|---|
| Thinking | on / off |
| Streaming | on / off |
| Backend | Transformers referência / vLLM / backend alvo alternativo |
| Precisão | BF16 ou FP16 referência / quantização alvo |
| Contexto | 8K / 32K / 64K / 128K; 262K só no perfil long |
| Modalidade | texto / imagem; vídeo se estiver no escopo |
| Calls | zero / uma / paralelas / multi-step |
| Idioma | português / inglês / mistura controlada |
| Concorrência | 1 / carga típica / saturação |

## 12. Baseline oficial de referência, não perfil local

As fases abaixo continuam úteis para integrar o checkpoint oficial. Para o
perfil `mitos`/Ollama já medido, prevalecem
[`DECISOES-2.0.md`](../DECISOES-2.0.md) e
[`model-profiles.json`](../../config/model-profiles.json).

### Fase 1: protocolo antes de performance

- checkpoint oficial pós-treinado e revision fixada;
- Transformers/processor como oráculo de renderização;
- vLLM ou SGLang fixado por imagem/commit;
- contexto 32K ou 64K;
- somente texto com `--language-model-only`, se visão não for requisito inicial;
- `enable_thinking=false` como baseline de depuração e um perfil `true` separado;
- 5–10 tools simples, distintas e em `snake_case`;
- Chat Completions non-streaming primeiro;
- validador e executor sandboxed;
- golden eval antes de habilitar streaming.

### Fase 2: qualidade agentic

- comparar thinking on/off com parâmetros oficiais;
- adicionar multi-step e paralelismo com correlação interna;
- compactação de tool results e contexto;
- português e casos de não chamar tool;
- fallback: se o backend deixar XML em `content`, registrar falha e usar parser compatível somente se o payload estiver estruturalmente completo; nunca regex permissiva que execute texto arbitrário.

### Fase 3: escala e multimodal

- streaming com testes diferenciais;
- prefix caching/MTP somente após benchmark;
- 128K/262K em pools próprios;
- imagem e vídeo com quotas do processor;
- quantização apenas após paridade funcional e de segurança;
- canary e rollback em qualquer atualização de pesos, tokenizer, parser ou runtime.

## 13. Decisões que o projeto deve registrar

1. ID/revision exatos do checkpoint.
2. Backend e versão/digest.
3. Chat Completions ou Responses API; não presumir paridade.
4. Thinking default e política de exposição/log.
5. Contexto máximo operacional por pool.
6. Suporte ou não a imagem/vídeo.
7. Parser nativo, parser do backend e fallback.
8. Schema interno de messages/tool calls/results.
9. Convenção de nomes e registry das tools.
10. Política de execução, aprovação, sandbox e rede.
11. Estratégia de compactação de contexto.
12. Eval gate para releases e atualizações.

## Fontes primárias principais

Todas acessadas em **2026-08-09**.

### Qwen

- [Qwen/Qwen3.5-4B — model card](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Qwen/Qwen3.5-4B — config.json](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json)
- [Qwen/Qwen3.5-4B — tokenizer_config.json e chat template](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json)
- [Qwen/Qwen3.5-4B — índice dos pesos](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/model.safetensors.index.json)
- [Qwen/Qwen3.5-4B-Base](https://huggingface.co/Qwen/Qwen3.5-4B-Base)
- [Qwen3.5 — blog oficial](https://qwen.ai/blog?id=qwen3.5)
- [Qwen-Agent — repositório](https://github.com/QwenLM/Qwen-Agent)
- [Qwen-Agent — demo Qwen3.5](https://github.com/QwenLM/Qwen-Agent/blob/main/examples/assistant_qwen3.5.py)
- [Qwen-Agent — Tool Introduction](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/tool/)
- [Qwen-Agent — Configuration](https://qwenlm.github.io/Qwen-Agent/en/guide/get_started/configuration/)
- [Qwen-Agent — Context Management](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/context/)
- [Qwen Code — tools](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/introduction/)
- [Qwen Code — file system tools](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/file-system/)
- [Qwen Code — settings e nomes canônicos](https://github.com/QwenLM/qwen-code/blob/main/docs/users/configuration/settings.md)
- [Qwen Code — sandbox](https://qwenlm.github.io/qwen-code-docs/en/users/features/sandbox/)

### Runtimes

- [vLLM — receita Qwen3.5](https://github.com/vllm-project/recipes/blob/main/Qwen/Qwen3.5.md)
- [vLLM — parser Qwen3 reasoning/tools](https://github.com/vllm-project/vllm/blob/main/vllm/parser/qwen3.py)
- [vLLM — GHSA-79j6-g2m3-jgfw/CVE-2025-9141, RCE no parser `qwen3_coder`](https://github.com/vllm-project/vllm/security/advisories/GHSA-79j6-g2m3-jgfw)
- [Transformers — inconsistências de templates de tool calling](https://github.com/huggingface/transformers/issues/45419)
- [llama.cpp — conversor HF → GGUF](https://github.com/ggml-org/llama.cpp/blob/master/convert_hf_to_gguf.py)

### Issues de upstream usadas como casos de regressão, não como especificação

- [vLLM #31871 — streaming/raw tool calls](https://github.com/vllm-project/vllm/issues/31871)
- [vLLM #42021 — reasoning + tools em Qwen3.5-27B](https://github.com/vllm-project/vllm/issues/42021)
- [vLLM #48753 — whitespace em argumentos](https://github.com/vllm-project/vllm/issues/48753)
- [llama.cpp #23758 — conversão de checkpoint intermediário](https://github.com/ggml-org/llama.cpp/issues/23758)
- [llama.cpp #24737 — fine-tune Qwen3.5-4B e contagem de blocos](https://github.com/ggml-org/llama.cpp/issues/24737)
