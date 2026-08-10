# Cruzamento da herança empírica com a pesquisa do Qwen3.5-4B

**Data do cruzamento:** 9 de agosto de 2026  
**Evidência empírica:** cerca de 150 turnos de bancada e 90 turnos de produção instrumentados  
**Escopo:** reconciliar o dossiê do harness 1.0 com a pesquisa técnica, a arquitetura e o catálogo de tools já escritos para o 2.0

> Este relatório preserva a análise e a proveniência. O contrato vigente foi
> condensado em [`DECISOES-2.0.md`](../DECISOES-2.0.md) e nos arquivos de
> [`config/`](../../config/).

> **Correção posterior do dono do projeto:** shell não é uma proibição
> permanente. Ela fica fora do baseline atual e pode entrar em perfil futuro
> após sandbox isolado, aprovação/policy, limites, auditoria, idempotência e
> avaliações de segurança.

## Veredito executivo

O dossiê não valida diretamente o checkpoint oficial `Qwen/Qwen3.5-4B`. Ele mede
`mitos:latest`, descrito como um Qwen3.5-4B **abliterado**, com artefato de 3,5 GB,
servido pelo Ollama numa Radeon RX 7600 e com contexto de 24.576 tokens. O método de
quantização, o digest do modelo, o template efetivamente usado e a versão do Ollama
não estão registrados no dossiê. Portanto, resultados de sampling, obediência e
tool calling devem ser tratados como válidos primeiro para essa combinação, não
como propriedades universais do checkpoint oficial. O [model card oficial](https://huggingface.co/Qwen/Qwen3.5-4B)
e o [índice oficial de pesos](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/model.safetensors.index.json)
descrevem outro artefato: pós-treinado oficial, multimodal e com aproximadamente
8,68 GiB de pesos BF16.

Mesmo com essa limitação, o cruzamento muda decisões concretas do 2.0:

- o perfil local de tools deve partir de `presence_penalty=0`, e não dos perfis
  gerais com 1,5 ou 2,0 publicados pelo Qwen;
- `temperature=0.3` é um baseline herdado, não um ótimo demonstrado, pois não houve
  braço comparativo de temperatura;
- o prompt lido pelo modelo parte em inglês e pede resposta em português, mas o
  ganho de 27 pontos precisa de réplica que isole idioma de comprimento;
- paths apresentados ao modelo devem ser relativos ao workspace; o exemplo de path
  absoluto da versão inicial de `TOOLS_PARA_QWEN35.md` já foi substituído;
- shell não entra no catálogo inicial, ainda que `run_shell_command` seja nome
  canônico no Qwen Code; a capacidade fica adiada atrás de gates próprios;
- com oito tools não há demanda medida para seleção dinâmica; catálogo pequeno e
  derivado do registry é o baseline;
- limites e contenções são mecânicos. Avisar o modelo, devolver erro de teto ou
  pedir que pare não conta como guarda;
- deduplicação, extração única de HTML, view derivada e calibragem por backend são
  requisitos sustentados por volume de produção;
- a reprodução fiel do perfil Ollama mantém `think:true`, porque foi assim que os
  números locais foram obtidos; `enable_thinking=false` é braço novo e baseline
  de depuração apenas para a integração vLLM;
- verificação automática de HTML uma vez por SHA continua sendo a principal
  hipótese ainda não provada;
- LangGraph, `SqliteSaver`, AG-UI, `assistant-ui`, SQLite local sem conteúdo e
  LangSmith desligado já são decisões do produto, não escolhas reabertas pela
  pesquisa do modelo.

## Método e classes de conclusão

Foram confrontados quatro documentos:

1. [HERANÇA §1–12](../../HERANCA-PARA-O-2.0.md), evidência empírica e decisões do
   produto;
2. [pesquisa técnica do Qwen3.5-4B](QWEN35_4B_PESQUISA.md), baseada em fontes
   primárias do Qwen e dos runtimes;
3. [guia de arquitetura](../HARNESS_QWEN35_4B.md), que transforma a pesquisa em
   baseline técnico;
4. [guia de nomes e schemas](../TOOLS_PARA_QWEN35.md), que propõe o catálogo
   inicial.

Cada conclusão recebe exatamente uma classe primária:

| Classe | Significado neste documento |
|---|---|
| **CONFIRMA FONTE OFICIAL** | A medição local aponta na mesma direção de um contrato ou recomendação documentada pelo mantenedor. O número continua local. |
| **CONTRADIZ/RECALIBRA RECOMENDAÇÃO** | A evidência local é suficiente para mudar o baseline deste produto, mesmo sem invalidar a recomendação geral do fornecedor. |
| **DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO LOCAL** | O efeito está confundido com `mitos`, abliteração, quantização, Ollama, hardware ou configuração não comparada. Não deve ser transferido sem réplica. |
| **PERMANECE NÃO PROVADO** | É decisão, hipótese ou recomendação sem medição válida que demonstre o efeito alegado. Pode ser implementado atrás de métrica. |

### Ressalvas metodológicas globais

- O universo informado é de **~150 turnos de bancada** e **90 turnos de produção**;
  os braços menores usam de 9 a 18 turnos. Uma diferença de um erro nesses braços
  não deve ser tratada como efeito estável. [HERANÇA §1](../../HERANCA-PARA-O-2.0.md#1-o-que-este-arquivo-é-e-o-que-não-é)
  e [§9](../../HERANCA-PARA-O-2.0.md#9-como-medir-no-20).
- Turnos e chamadas dentro da mesma sessão não são necessariamente observações
  independentes. O dossiê não registra intervalos de confiança, seeds, ordem
  aleatorizada nem digest completo do sistema.
- O A/B de idioma alterou também o tamanho do prompt em quatro linhas e sua métrica
  de erro conflou path inválido com sequência inválida. Ele identifica direção,
  não magnitude causal.
- A comparação de `presence_penalty` manteve `temperature=0.3`, mas não estabelece
  se há interação entre os dois parâmetros.
- Métricas de produção têm alta validade para a carga real do 1.0, porém não isolam
  causalmente cada mecanismo. Métricas de bloco não equivalem a tarefas
  independentes.
- Não existe ablação oficial de nomes de tools para o Qwen3.5-4B. O vocabulário do
  Qwen Code é evidência de compatibilidade, não prova causal de superioridade.

## Matriz de reconciliação

| Ponto | Evidência do dossiê | Relação com os documentos e fontes oficiais | Classe primária | Consequência para o 2.0 |
|---|---|---|---|---|
| Identidade do modelo | `mitos:latest`, Qwen3.5-4B abliterado, 3,5 GB, Ollama; quantização e digest não informados | A pesquisa assume `Qwen/Qwen3.5-4B` pós-treinado oficial, cujo artefato BF16 tem ~8,68 GiB | **DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO LOCAL** | Criar manifesto do sistema antes de transferir qualquer taxa do 1.0 |
| `presence_penalty` | `pp=1.1`: 5/18 turnos sem tool, 5 `write_file`, arquivo de 2.349 B; `pp=0`: 2/18, 9 e 3.921 B | O Qwen recomenda 1,5 em perfis gerais e 2,0 em um perfil sem thinking; 0,0 apenas em thinking/coding preciso | **CONTRADIZ/RECALIBRA RECOMENDAÇÃO** | Perfil local de tools começa em `pp=0`; perfil oficial fica como controle, não default |
| `temperature=0.3` | Usada em toda a medição; não houve A/B de temperatura | Perfis oficiais publicados usam 0,6, 0,7 ou 1,0 | **PERMANECE NÃO PROVADO** | Preservar como baseline de reprodução e comparar isoladamente com 0,6/0,7 |
| Contexto e RX 7600 | 24.576 tokens, 7,98 GB VRAM, 100% GPU, 68 tok/s; `granite4.1:8b` derramou 17% para CPU e levou 40,7 s/turno contra 20,2 do 4B; Qwen3.5-9B não coube | O contexto oficial de 262.144 é capacidade, não requisito; a pesquisa já recomenda medir janelas menores | **DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO LOCAL** | `ctx=24576` é o perfil operacional da máquina atual; 32K+ exige medição/OOM gate |
| Prompt EN versus PT | 15 turnos por braço: desobediência 67% PT e 40% EN; 0/14 respostas em inglês com ordem final em PT; 25,8 versus 29,9 s/turno | A pesquisa pede eval bilíngue, e a versão inicial do guia admitia descrição no idioma da sessão | **CONTRADIZ/RECALIBRA RECOMENDAÇÃO** | System prompt baseline em inglês, saída em português; descrições de tool continuam experimento separado |
| Paths relativos | Erros 7/18 sem frase e 0/18 com frase que exige path relativo e recusa absoluto | A versão inicial do exemplo de `read_file` dizia “caminho absoluto”; Qwen Code só sustenta o nome `file_path`, não obriga a semântica absoluta | **CONTRADIZ/RECALIBRA RECOMENDAÇÃO** | Modelo usa path relativo; executor resolve, segue symlink e confina ao workspace |
| Oito tools e seleção dinâmica | O 1.0 expôs oito; não há demanda medida para roteamento dinâmico | A versão inicial do guia propunha 6–10 por rota e `tool_search`/roteador quando o catálogo crescesse | **CONTRADIZ/RECALIBRA RECOMENDAÇÃO** | Começar com registry pequeno e estático por capacidade; não portar exatamente as oito antigas nem criar roteador sem número |
| Nomes das tools | O dossiê mede capacidades, não A/B de nomes; lista deve vir do registry real | Qwen Code usa `read_file`, `write_file`, `edit`, `glob`, `grep_search`, `list_directory`, `web_fetch`, `web_search` etc. | **PERMANECE NÃO PROVADO** | Usar nomes canônicos como candidatos iniciais, removendo tools proibidas e escondendo motores internos |
| Shell | O modelo inventou shell 14 vezes diante de erro mudo; listar tools existentes levou à correção no passo seguinte; shell nunca existiu | Qwen Code oferece `run_shell_command` com sandbox, e a versão inicial do guia o colocou como P0 | **CONTRADIZ/RECALIBRA RECOMENDAÇÃO** | Não expor no baseline; adiar até sandbox/policy/evals; erros desconhecidos enumeram somente as tools reais |
| Guardas de loop | Aviso de repetição falhou em 27% das chamadas; teto por alvo emitiu 39 avisos e chegou a 12 chamadas apesar do teto 5; prompt foi desobedecido em 33%–78% | As fontes e o guia exigem limites no harness, allowlist e kill switch | **CONFIRMA FONTE OFICIAL** | Ação deixa de ser oferecida ou o turno termina; mensagem ao modelo é explicação, não contenção |
| Teto numérico do turno | Operação do 1.0 usou 15 passos e, no último, removeu todas as tools | O guia sugeriu 8 apenas como ponto de partida | **PERMANECE NÃO PROVADO** | Herdar 15 para comparação; reduzir para 8 somente por A/B com sucesso, latência e abandono |
| Encerrar ao repetir o mesmo alvo | Duas tentativas de medir foram inválidas; uma quase não reproduziu a patologia, outra caiu por bug de geração | O guia recomenda encerrar em limite/erro, mas não prova este gatilho | **PERMANECE NÃO PROVADO** | Implementar atrás de flag; não alegar benefício antes de bancada válida |
| Dedup por referência | 1.264 blocos: 1.279.397 → 19.933 tokens, **−98%** | Qwen-Agent recomenda comprimir tool responses e administrar contexto | **CONFIRMA FONTE OFICIAL** | Requisito no hook pré-modelo; original permanece no histórico/checkpoint |
| Extração de HTML | 56 blocos: 36.723 → 3.853 tokens, **−90%**; deve existir em um só lugar | A pesquisa recomenda limitar/compactar resultados não confiáveis | **CONFIRMA FONTE OFICIAL** | Uma função de extração na camada de contexto; HTTP/navegador compartilham-na |
| Corte por orçamento | 141/415 montagens (**34%**) precisaram descartar mensagens antigas na view | O limite oficial inclui entrada e saída; Qwen-Agent documenta gestão de contexto | **CONFIRMA FONTE OFICIAL** | Um único orçamento igual ao contexto efetivo do backend, com saída reservada |
| Calibrador de tokens | 399 amostras; erro relativo 1,8%; overhead aprendeu 4,0 → 9,74 tokens/mensagem | Template e schemas compõem o prompt, e o backend devolve contagem real distinta | **DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO LOCAL** | Calibração persistida separadamente por backend/modelo/template |
| Thinking como knob | `think: true` fixo fazia backends sem suporte retornarem 400 antes do primeiro token | O Qwen3.5 pensa por padrão e documenta `enable_thinking`; a sintaxe varia por backend | **CONFIRMA FONTE OFICIAL** | Capability negotiation e perfil explícito; nunca literal universal no payload |
| Melhor modo de thinking | O dossiê não compara thinking on/off para sucesso agentic; os números Ollama foram obtidos com `think:true` | A arquitetura escolhe `enable_thinking=false` só como baseline de depuração | **PERMANECE NÃO PROVADO** | Reproduzir Ollama com `true`; iniciar a integração vLLM com `false`; medir on/off antes de promover |
| Verificação automática 1×/SHA | A versão como tool elevou 18,6 → 194 s/turno, teve 61% de repetição e matou 6/12 turnos; a versão automática ainda não foi medida | A arquitetura recomenda idempotência/checksum, mas não demonstra essa solução | **PERMANECE NÃO PROVADO** | Implementar hipótese decidida, registrar SHA e comparar contra ambas as linhas de base |
| Métrica sem conteúdo | 90 turnos/415 contextos/188 calls só foram diagnosticáveis por instrumentação; política local proíbe conteúdo | A arquitetura pede audit log com argumentos redigidos e recomenda não logar reasoning | **CONFIRMA FONTE OFICIAL** | SQLite local é fonte de verdade; gravar metadados, tamanhos e hashes, não corpos |
| LangGraph + `SqliteSaver` | Decisão do produto; nenhuma implementação/medição do 2.0 ainda | É compatível com histórico imutável + view derivada, mas não é fato do Qwen | **PERMANECE NÃO PROVADO** | Manter a decisão e medir custo/correção; não reabrir por preferência |
| AG-UI + `assistant-ui` | Comparação prática mostrou tools/reasoning no painel; dois desvios de schema foram detectados no cliente real | Não há conflito com o contrato do Qwen; é escolha de protocolo/UI | **PERMANECE NÃO PROVADO** | Manter AG-UI nativo e `CUSTOM`; reconstruir replay bench antes do modelo |

## 1. Checkpoint, runtime e sampling

### 1.1 A fronteira de validade é o sistema inteiro

**Classe: DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO LOCAL.**

O [dossiê §3](../../HERANCA-PARA-O-2.0.md#3-o-modelo-qwen35-4b-medido)
identifica `mitos:latest` como “qwen3.5 4b abliterado”, com 3,5 GB, mas não
registra:

- digest imutável do blob e base exata do fine-tune;
- método/nível de quantização;
- template do Ollama e parser de tool calling;
- versão do Ollama, driver/ROCm e flags efetivas;
- valores herdados do Modelfile além do alerta sobre `presence_penalty`.

A abliteração pode mudar recusa, obediência e distribuição da resposta; a
quantização pode mudar seleção, tokens estruturais e argumentos exatos; o Ollama
pode mudar template e tradução de parâmetros. Isso coincide com a política de
versionamento já recomendada na [pesquisa §5.3](QWEN35_4B_PESQUISA.md#53-política-de-versões)
e na [arquitetura, “Quantização”](../HARNESS_QWEN35_4B.md#quantização): modelo,
tokenizer, template, runtime, parser e quantização formam uma única versão do
sistema. O [config oficial](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json)
e o [template oficial](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json)
são oráculos do checkpoint oficial, não prova do que o Ollama entregou a `mitos`.

Antes de usar os números como gate de uma nova combinação, o 2.0 precisa gravar
um manifesto reproduzível. Até lá, “Qwen 4B medido” significa estritamente
`mitos`/Ollama/RX 7600/configuração do dossiê.

### 1.2 `presence_penalty=0` vence o default oficial no perfil local de tools

**Classe: CONTRADIZ/RECALIBRA RECOMENDAÇÃO.**

No A/B de **18 turnos por braço**, `pp=1.1` produziu 5 turnos sem chamada,
5 chamadas a `write_file` e arquivo final de 2.349 bytes. Com `pp=0`, foram 2
turnos sem chamada, 9 `write_file` e 3.921 bytes. A temperatura ficou em 0,3.
O mecanismo proposto — penalizar tokens que reaparecem dentro do argumento longo
da tool — é plausível, mas não foi isolado por inspeção de logits nem repetido em
outro backend. [HERANÇA §3, “presence_penalty”](../../HERANCA-PARA-O-2.0.md#presence_penalty-acima-de-zero-faz-o-modelo-abandonar-a-ferramenta).

O [Qwen recomenda](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices)
`presence_penalty=1.5` em thinking geral e não-thinking geral, 2,0 em um perfil
de reasoning sem thinking e 0,0 em thinking para coding preciso. O próprio card
alerta que valores altos podem misturar idiomas e degradar levemente qualidade.
A evidência local é específica, mas diretamente alinhada à tarefa central do
produto: selecionar e preencher tools com payloads de arquivo.

Decisão reconciliada:

- `local_mitos_ollama_reproduction`: `presence_penalty=0`;
- perfis oficiais com 1,5/2,0: controles de avaliação, nunca defaults silenciosos;
- toda comparação registra calls vazias, seleção, bytes do argumento, sucesso fim
  a fim e mistura de idioma;
- o boot deve materializar os parâmetros efetivos, pois o dossiê alerta que
  `jaahas/qwen3.5-uncensored:4b` trazia `presence_penalty 1.5` no Modelfile.

Isso substitui a neutralidade da [arquitetura, “Perfis de geração”](../HARNESS_QWEN35_4B.md#perfis-de-geração)
para o perfil local de tools, sem declarar que o Qwen oficial é melhor globalmente
com `pp=0`.

### 1.3 `temperature=0.3` é controle histórico, não conclusão

**Classe: PERMANECE NÃO PROVADO.**

O dossiê afirma que temperatura baixa torna a seleção mais confiável, mas 0,3 foi
usado **em toda a medição**; não existe braço 0,3 versus 0,6/0,7/1,0. O resultado
preserva uma configuração reprodutível, não estima seu efeito. Os perfis do
[model card](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices) usam 0,6 para
thinking/coding preciso, 0,7 sem thinking geral e 1,0 nos perfis gerais de
reasoning.

O primeiro A/B útil mantém `pp=0`, prompt, tarefas, ordem, checkpoint, seed quando
suportada e limites iguais, variando somente a temperatura. Até lá, 0,3 deve ser
preservado para reproduzir a linha de base do 1.0 e não descrito como ótimo do
Qwen3.5-4B.

### 1.4 24.576 é o contexto medido nesta AMD

**Classe: DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO LOCAL.**

Na Radeon RX 7600 de 7,98 GB, o artefato local de 3,5 GB executou 100% em GPU com
`ctx=24576` e aproximadamente 68 tok/s. Um Granite 8B com 5,35 GB de pesos chegou
a 7,7 GB, derramou 17% para CPU e duplicou aproximadamente a latência por turno
(20,2 → 40,7 s); o Qwen3.5-9B de 6,59 GB não coube. Esses dados corrigem a antiga
inferência “sem NVIDIA = sem GPU”, mas não dimensionam BF16/vLLM nem outra
quantização. [HERANÇA §3, “Máquina e teto real”](../../HERANCA-PARA-O-2.0.md#máquina-e-teto-real)
e [§7](../../HERANCA-PARA-O-2.0.md#7-as-premissas-erradas-e-o-que-cada-uma-custou).

O contexto nativo de 262.144 no [model card](https://huggingface.co/Qwen/Qwen3.5-4B#processing-ultra-long-texts)
é capacidade do checkpoint. Não é configuração viável automática. A pesquisa
estima apenas o KV das oito camadas de atenção completa em ~1 GiB BF16 a 32K e
~8 GiB a 262K, antes dos outros estados. Já o artefato oficial pesa ~8,68 GiB
BF16. Assim, o dado local **confirma a direção** de usar contexto reduzido, mas
seu número depende inteiramente do artefato/runtime.

Para a máquina-alvo atual, o baseline deve ser 24.576, com orçamento único para
entrada + saída. Degraus de 32K/64K só entram após medir VRAM real no serviço,
latência, OOM e spill. Consultar o próprio serviço/driver ROCm é a medição; nunca
usar ausência de `nvidia-smi` como evidência.

### 1.5 Thinking precisa ser capacidade negociada; a qualidade on/off está aberta

Há duas conclusões diferentes:

1. **CONFIRMA FONTE OFICIAL:** um literal `think: true` no payload fez backends
   não-Qwen retornarem `400 does not support thinking` antes do primeiro token.
   O Qwen3.5 documenta thinking por padrão e `enable_thinking=false`, mas a posição
   do campo varia entre vLLM/SGLang e serviços. O knob explícito e adaptado por
   backend é obrigatório. [HERANÇA §3](../../HERANCA-PARA-O-2.0.md#raciocínio-explícito-não-é-universal),
   [model card, modos](https://huggingface.co/Qwen/Qwen3.5-4B#instruct-or-non-thinking-mode)
   e [pesquisa §3.2](QWEN35_4B_PESQUISA.md#32-thinking-mode).
2. **PERMANECE NÃO PROVADO:** o dossiê não compara sucesso, calls, latência ou
   parsing com thinking on/off. Para reproduzir a linha Ollama/`mitos`, o braço de
   controle precisa manter o `think:true` literal que gerou os números. Somente a
   integração nova em vLLM começa com `enable_thinking=false` como baseline de
   depuração contra riscos do parser; isso não o torna vencedor de qualidade.

O reasoning antigo não deve voltar ao prompt, conforme a
[recomendação oficial](https://huggingface.co/Qwen/Qwen3.5-4B#best-practices), e
não deve ser persistido/exposto por default por privacidade. O painel pode exibir
reasoning somente quando o backend e a política o permitirem; isso não altera o
registro canônico da conversa.

## 2. Prompt, paths e catálogo de tools

### 2.1 Inglês para o modelo, português para a pessoa

**Classe: CONTRADIZ/RECALIBRA RECOMENDAÇÃO.**

Com **15 turnos por braço**, mudar o system prompt de 25 linhas em português para
21 em inglês reduziu a desobediência medida de 67% para 40%. A última linha pedia
resposta em português e segurou o idioma em 14/14 respostas observadas. A latência
subiu de 25,8 para 29,9 s/turno. [HERANÇA §3, “Prompt em inglês”](../../HERANCA-PARA-O-2.0.md#prompt-em-inglês-obedece-mais).

O resultado tem dois confundidores declarados: quatro linhas foram removidas junto
da tradução, e a métrica misturou path incorreto com ordem incorreta de operações.
Logo, “27 pontos” não é efeito puro de idioma. Ainda assim, a direção é forte o
bastante para o baseline deste modelo local:

- system prompt e instruções de controle em inglês;
- nomes de tools em inglês `snake_case`;
- última instrução explícita para responder em português;
- documentação do projeto em português;
- A/B futuro 2×2: PT/EN × longo/enxuto, com o mesmo conteúdo semântico.

A orientação do [guia de tools](../TOOLS_PARA_QWEN35.md#anti-padrões) de manter
nomes em inglês permanece. A frase da versão inicial, “descrição no idioma da
sessão”, não está demonstrada para este harness: idioma de descrição/schema deve
ser testado separadamente, sem inferi-lo do A/B de system prompt. O guia já foi
atualizado para usar inglês model-facing como baseline local. O
[benchmark oficial multilíngue](https://huggingface.co/Qwen/Qwen3.5-4B#benchmark-results)
mostra capacidade agregada, não taxa de obediência em português com estas tools.

### 2.2 O contrato deve dizer “path relativo”

**Classe: CONTRADIZ/RECALIBRA RECOMENDAÇÃO.**

Adicionar uma frase dizendo que o path é relativo ao workspace e que absolutos
são recusados levou erros de path de **7/18 para 0/18**. Foi a eliminação mais
categórica observada, embora num único checkpoint/runtime. [HERANÇA §3, “Uma
frase no prompt”](../../HERANCA-PARA-O-2.0.md#uma-frase-no-prompt-eliminou-uma-classe-de-erro).

Isso supera a redação inicial do exemplo de `read_file` no
[guia de schemas](../TOOLS_PARA_QWEN35.md#exemplo-leitura), que descrevia
“caminho absoluto dentro do workspace permitido”, e o exemplo absoluto da
[pesquisa §4.2](QWEN35_4B_PESQUISA.md#42-representação-canônica-recomendada).
O nome oficial `file_path` pode ser mantido; sua semântica local muda para path
relativo.

Contrato reconciliado:

```json
{
  "file_path": {
    "type": "string",
    "description": "Path relative to the workspace root. Absolute paths and paths outside the workspace are rejected."
  }
}
```

O executor continua responsável por resolver o path, seguir symlinks, verificar
que o destino real está dentro da jaula e recusar traversal. Prompt reduz erro;
jaula garante segurança. A bancada usa raiz curta e comum, pois o dossiê relata
que um path temporário longo contaminou os dois braços de um A/B.

### 2.3 O número oito dispensa roteamento dinâmico por enquanto

**Classe: CONTRADIZ/RECALIBRA RECOMENDAÇÃO.**

O 1.0 tinha oito capacidades: ler, escrever, listar, apagar, buscar web, baixar
página, verificar página e descrever imagem; as duas últimas/condicionais eram
ligadas por configuração. O próprio dossiê determina que o conjunto do 2.0 será
novo e que esses limiares são referência, não spec. Com apenas oito tools, não
foi registrada demanda que justificasse seleção dinâmica. [HERANÇA §2](../../HERANCA-PARA-O-2.0.md#2-decisões-do-20-já-tomadas),
[§6](../../HERANCA-PARA-O-2.0.md#sem-demanda-medida-pode-entrar-mas-com-métrica-na-frente)
e [§8](../../HERANCA-PARA-O-2.0.md#8-ferramentas-comportamento-medido-e-limiares-calibrados).

Isso recalibra “seleção do pequeno conjunto permitido para a rota” no
[guia de arquitetura](../HARNESS_QWEN35_4B.md#arquitetura-do-loop) e o uso de
`tool_search` na versão inicial do
[guia de tools](../TOOLS_PARA_QWEN35.md#catálogo-aprovado-para-o-harness-20):

- o registry real gera schemas e a lista mencionada no prompt;
- o modelo recebe todo o pequeno conjunto permitido pela política da sessão;
- uma tool ligada nunca é escondida por uma lista manual, e uma desligada nunca é
  citada;
- roteamento dinâmico só é experimentado quando o catálogo ou a métrica de seleção
  demonstrar o problema;
- avaliações 5/10/20/40 continuam válidas para descobrir o ponto de quebra, mas
  não justificam implementar o roteador antes dele.

### 2.4 Nomes oficiais são candidatos; “melhor nome” continua aberto

**Classe: PERMANECE NÃO PROVADO.**

O dossiê não compara nomes. Portanto, continua válida a melhor evidência primária
disponível: o [catálogo oficial do Qwen Code](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/tools/tool-names.ts)
e a [documentação oficial de tools](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/introduction/).
Usar ASCII `snake_case`, verbo + objeto e sem aliases simultâneos reduz ambiguidade
com o wire `<function=NAME>` do
[template oficial](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json).

Para o 2.0, quando a semântica existir, os candidatos iniciais continuam sendo:

| Capacidade pública | Nome exposto | Ajuste vindo da herança |
|---|---|---|
| ler arquivo | `read_file` | `file_path` relativo, paginação e jaula |
| escrever arquivo inteiro | `write_file` | verificação HTML automática fora do catálogo |
| editar trecho | `edit` | pré-condição/hash e round-trip de whitespace |
| listar diretório | `list_directory` | path relativo e limite |
| localizar paths | `glob` | distinguir de conteúdo |
| buscar conteúdo | `grep_search` | distinguir de web |
| buscar na web | `web_search` | resultado honesto: sucesso, vazio ou bloqueio |
| obter página conhecida | `web_fetch` | começa em HTTP e escala internamente por sintoma |
| descrever imagem, se habilitada | nome a avaliar (`describe_image` ou canônico de visão) | retorno e resposta final marcados como inferência de outro modelo |

Não entram no baseline `run_shell_command`, uma tool pública de
navegador/verificação nem aliases de transporte. `run_shell_command` pode entrar
em perfil futuro após seus gates. `delete_file` exige decisão explícita de
produto e confirmação; ter existido no 1.0 não o torna obrigatório no 2.0.

### 2.5 Shell fica adiada, mesmo com nome e sandbox oficiais

**Classe: CONTRADIZ/RECALIBRA RECOMENDAÇÃO.**

O [guia de tools](../TOOLS_PARA_QWEN35.md#catálogo-aprovado-para-o-harness-20) colocou,
em sua versão inicial, `run_shell_command` como P0 por ser canônico no Qwen Code.
A herança contém uma decisão de segurança mais restritiva para o baseline:
jaula de path não confina shell e o primeiro release não precisa desse poder. O
modelo inventou uma tool de shell **14 vezes** quando
recebia erro mudo; depois que o erro listou as tools existentes, corrigiu-se no
passo seguinte. [HERANÇA §6, “Ferramenta honesta” e “Sem ferramenta de shell”](../../HERANCA-PARA-O-2.0.md#proibido-quebrar-custa-dado-ou-segurança).

O Qwen Code documenta sandbox e aprovação justamente porque shell é sensível;
essa é uma opção do agente oficial, não requisito de compatibilidade. A
[documentação oficial](https://qwenlm.github.io/qwen-code-docs/en/users/features/sandbox/)
afirma que sandbox reduz risco, sem tornar a operação necessária para outros
produtos. No baseline, operações necessárias viram tools tipadas. Tool
desconhecida retorna erro curto com o conjunto real; não se cria shell para
satisfazer a alucinação. Um perfil futuro só promove `run_shell_command` após
sandbox por tarefa, aprovação/policy, limites, auditoria, reconciliação após
timeout e regressões de segurança.

### 2.6 Fetch, navegador e visão são capacidades diferentes

Os números de web são calibração operacional, não contrato universal de site:

- Wikipédia: 160.820 caracteres por HTTP simples, navegador nunca necessário;
- sites JS: 3/3, 3/3 e 4/4 exigiram navegador;
- uma galeria: 12/30 exigiram navegador, apesar do mesmo domínio;
- menos de 120 caracteres após extração é erro calibrado, com o pouco texto
  preservado;
- espera 5, 10, 20, 40 e 80 segundos, no máximo cinco tentativas;
- cache por turno, com URL exata e busca normalizada.

**Classe: DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO LOCAL.** Os limiares e
domínios são locais. Esconder detalhes do executor atrás de uma tool pequena e
devolver erro honesto/limitado também está alinhado às fontes oficiais, mas não
muda a classe primária deste ponto operacional. [HERANÇA §8](../../HERANCA-PARA-O-2.0.md#baixar-página-quando-o-navegador-é-necessário)
e [pesquisa §10](QWEN35_4B_PESQUISA.md#10-segurança-operacional).

O modelo não decide HTTP versus navegador porque não conhece o resultado antes
da execução. `web_fetch` faz HTTP primeiro e escala por sintoma internamente. Os
limiares devem continuar instrumentados por domínio/motor e ser recalibrados se
o extrator, navegador ou corpus mudar.

Descrição de imagem é resultado probabilístico de outro modelo. Um PNG com
`IMC 24.7` virou `IMC 247`; por isso o resultado no histórico e a resposta final
precisam de avisos gerados pelo harness. O número é um caso, não taxa de erro.

## 3. Loop, contexto e verificação

### 3.1 Controle está no grafo, não no prompt

**Classe: CONFIRMA FONTE OFICIAL.**

Uma regra do system prompt foi desobedecida entre 33% e 78% dos turnos nos braços
observados; disponibilizar a tool de teste reduziu a desobediência medida a 0%.
Mais diretamente:

- anunciar repetição não evitou nova repetição em **27%** das calls;
- um contador com teto 5 emitiu **39** erros em um braço de 11 turnos e ainda
  chegou a **12** chamadas ao mesmo alvo;
- o pior caso repetiu a chamada idêntica sete vezes;
- pedir “pare” no prompt não conteve o modelo.

[HERANÇA §3](../../HERANCA-PARA-O-2.0.md#o-modelo-desobedece-o-prompt-entre-33-e-78-das-vezes)
e [§5](../../HERANCA-PARA-O-2.0.md#por-que-os-três-guardas-de-loop-falharam).

Isso reforça os controles deny-by-default, limites, idempotência e kill switch da
[pesquisa §10](QWEN35_4B_PESQUISA.md#10-segurança-operacional). A regra precisa
alterar o conjunto de transições possíveis:

- chamada fora da allowlist nunca executa;
- ao atingir o limite, as tools desaparecem e o modelo só pode finalizar;
- se o limite for terminal, o grafo encerra sem pedir cooperação;
- mutação nunca é repetida automaticamente sem prova/idempotency key;
- erro é histórico observável, não sucesso vazio;
- uma recusa de formato pode voltar ao modelo; duas recusas consecutivas sobem
  como incompatibilidade de modelo/configuração.

O teto **15** é o único valor operacional herdado. O ponto inicial **8** do guia
de arquitetura não foi medido; nem 15 foi comparado a outro teto. Começar com 15
preserva a linha de base e permite A/B posterior. No passo final, nenhuma tool é
oferecida.

### 3.2 Encerrar por repetição do mesmo alvo ainda não tem resultado válido

**Classe: PERMANECE NÃO PROVADO.**

As duas bancadas falharam metodologicamente: numa, a patologia disparou 2 vezes
contra 20 no controle, mas a latência caiu porque turnos ficaram mudos; na outra,
o processo caiu por geração recusada. Portanto, “encerre o turno no teto do mesmo
alvo” pode ser uma contenção segura, mas não tem efeito de qualidade/latência
medido. [HERANÇA §10](../../HERANCA-PARA-O-2.0.md#10-o-que-ficou-aberto-e-o-que-não-ficou-provado).

Ao experimentar, a implementação deve terminar mecanicamente — não devolver um
erro e manter a mesma tool disponível. O evento final precisa preservar o
desfecho `target_limit`, sem depois sobrescrevê-lo por `complete`.

### 3.3 A view derivada recebe quatro requisitos medidos

**Classe: CONFIRMA FONTE OFICIAL.**

Os números de produção vêm de **90 turnos, 415 montagens de contexto e 188
chamadas de ferramenta**. Eles dão corpo local à recomendação de gestão de
contexto do [Qwen-Agent](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/context/):

| Mecanismo | Amostra e resultado | Regra reconciliada |
|---|---|---|
| dedup por referência | 1.264 blocos; 1.279.397 → 19.933 tokens (**−98%**) | resultado repetido aponta para a primeira ocorrência na view |
| extração de HTML | 56 blocos; 36.723 → 3.853 (**−90%**) | uma implementação compartilhada por todos os motores |
| corte por orçamento | 141/415 montagens (**34%**) precisaram cortar | remover unidades antigas somente da view até caber entrada + saída |
| compressão de código | 99 blocos; **−28%** | manter se preservar bytes/semântica nos testes |
| calibragem | 399 amostras; erro relativo **1,8%**; overhead 4,0 → **9,74** | aprender contra contagem real do backend e persistir por combinação |

[HERANÇA §4](../../HERANCA-PARA-O-2.0.md#4-o-que-rendeu-com-o-número).

Há uma diferença importante em relação a uma leitura literal da política do
Qwen-Agent: o 2.0 **não remove** etapas do histórico persistido. O `SqliteSaver`
guarda o registro integral e o hook pré-modelo produz uma lista descartável com
dedup, extração, compressão e corte. Tentativa rejeitada também fica no histórico.
Isso preserva auditoria e impede “buracos” invisíveis. O orçamento usado pelo hook
é o mesmo contexto efetivamente enviado ao backend; não existem dois números que
possam divergir.

O calibrador recebe a classe primária **DEPENDE DO CHECKPOINT/BACKEND/QUANTIZAÇÃO
LOCAL** quando considerado isoladamente: 9,74 é overhead do template/Ollama
medido, não constante do Qwen. O requisito portável é recalibrar cada backend,
modelo, template e catálogo de schemas com a contagem real que o servidor retorna.

### 3.4 Não portar compressores e guardas sem ganho

**Classe: CONTRADIZ/RECALIBRA RECOMENDAÇÃO.**

“Compactar resultados/contexto” é boa orientação geral, mas não autoriza portar
qualquer compressor. Os resultados negativos foram:

- prosa: **5.579 blocos**, 4.818.608 → 4.797.226 tokens, arredondado a **−0%**;
- saída de shell: 3 blocos, **−0%**, apesar de não existir tool de shell;
- anúncio de chamada repetida: 27% continuaram repetindo;
- teto que apenas retorna erro: 39 estouros sem conter;
- verificação como tool: 18,6 → 194 s/turno, 61% de repetição e 6/12 turnos no
  limite.

[HERANÇA §5](../../HERANCA-PARA-O-2.0.md#5-o-que-não-rendeu-não-porte).

Sumarização/recorte de prosa por relevância é uma hipótese nova, não continuação
do compressor antigo. Só entra com métrica de fidelidade e economia. Avisos de
repetição podem existir como telemetria/explicação, mas nunca ser descritos como
controle.

### 3.5 Verificação de HTML por SHA é decisão e experimento

**Classe: PERMANECE NÃO PROVADO.**

A tool de navegador trouxe evidência contraditória: tornou a checagem real e levou
a desobediência da regra observada de 60% para zero, com uso em 9/12 turnos, mas
criou o loop `write_file → verificar → reescrever`, elevou latência para 194
s/turno, teve 61% de repetição e matou metade dos 12 turnos no limite. A saída
escolhida como candidata é automática:

1. depois de escrever HTML, o harness calcula SHA do conteúdo;
2. verifica cada SHA uma única vez;
3. injeta o resultado automaticamente;
4. não oferece tool de verificação ao modelo;
5. uma nova escrita produz novo SHA e, portanto, no máximo uma nova verificação.

[HERANÇA §5, “O caso central”](../../HERANCA-PARA-O-2.0.md#o-caso-central-o-loop-que-não-converge).

Essa arquitetura impossibilita a repetição da mesma versão e é coerente com
idempotência/checksum do guia, mas **nunca foi medida**. Por isso o rollout
começa em `disabled`; o nome histórico `automatic_once_per_sha` foi substituído
no contrato vigente por `automatic_once_per_page_revision`, com chave PageRevision, no braço definido em
[`evals/experiments.json`](../../evals/experiments.json). O experimento deve
comparar:

- sem verificação: 18,6 s/turno como linha histórica;
- tool controlada pelo modelo: 194 s/turno, 61% de repetição, 6/12 limites;
- automática 1×/SHA: latência, taxa de HTML válido, reescritas por SHA, passos,
  abandono e falsos positivos.

Cache de qualquer arquivo reescrito também usa SHA dos bytes, nunca somente path;
o dossiê encontrou descrição de screenshot obsoleta quando a chave era o caminho.

## 4. Métricas, privacidade e decisões fechadas

### 4.1 Instrumentação é parte do comportamento

**Classe: CONFIRMA FONTE OFICIAL.**

Os números que recalibram este documento só existem porque o 1.0 registrou calls,
montagens de contexto e turnos. Isso converge com as métricas recomendadas na
[pesquisa §11](QWEN35_4B_PESQUISA.md#11-plano-de-testes-do-harness) e na
[arquitetura, “Métricas e gates”](../HARNESS_QWEN35_4B.md#métricas-e-gates).

O 2.0 deve registrar, sempre com IDs de sessão/turno/passo:

- tool, alvo normalizado, sucesso, classe de erro, retentativa/repetição,
  tentativa, bytes, duração e decisão de política;
- estimativa de tokens, valor real, orçamento, mensagens/blocos antes/depois e
  custo de cada transformação;
- passos, limite e **um único desfecho final** que também é gravado em erro;
- domínio e motor de rede, caracteres por motor e motivo de escalada;
- backend/modelo e tokens de input, cache hit/miss, output e reasoning;
- digest de modelo/template/runtime/quantização e parâmetros efetivos.

O audit log de segurança pode exigir argumentos redigidos/checksums, mas não
corpos. O dossiê estabelece a política mais restritiva: instrumentação grava
endereço/identificador, tamanho, tempo e desfecho; conteúdo de arquivo, página,
prompt, resposta e reasoning permanece na sessão. Falha da telemetria avisa uma
vez e se desliga; nunca derruba o turno. [HERANÇA §6](../../HERANCA-PARA-O-2.0.md#proibido-quebrar-custa-dado-ou-segurança)
e [§9](../../HERANCA-PARA-O-2.0.md#9-como-medir-no-20).

### 4.2 SQLite local e backend remoto exigem consentimento explícito

**Classe: CONFIRMA FONTE OFICIAL.**

SQLite local **sem conteúdo** é a fonte de verdade; LangSmith fica atrás de knob
desligado. Isso concretiza os controles de segredos, redaction e auditabilidade da
[segurança oficial do Qwen Code](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/introduction/)
e da [pesquisa §10](QWEN35_4B_PESQUISA.md#10-segurança-operacional).

Backend remoto exporta todo o contexto em cada passo, não apenas o item que
disparou uma tool. A ativação deve ser explícita e visível. Backend inválido falha
no boot; não há fallback silencioso. Erros de saldo, chave e rate limit sobem
imediatamente, sem serem tratados como geração malformada. Custos são registrados
em tokens por categoria, não em dólares fixos, e o calibrador é separado por
backend. [HERANÇA §8, “Consentimento” e “Backend pago”](../../HERANCA-PARA-O-2.0.md#consentimento-é-explícito-e-barulhento).

### 4.3 LangGraph, AG-UI e `assistant-ui` não são decisões do modelo

**Classe: PERMANECE NÃO PROVADO** quanto a ganho de qualidade/latência; **decisão
de produto mantida** quanto à adoção.

O [dossiê §2](../../HERANCA-PARA-O-2.0.md#2-decisões-do-20-já-tomadas) fixa:

- LangChain/LangGraph no rewrite;
- estado no `SqliteSaver`;
- hook pré-modelo para a view derivada;
- AG-UI nativo;
- telemetria própria em evento `CUSTOM`;
- `assistant-ui` como painel escolhido;
- ADR + relatório de medição, sem specs numeradas por feature.

Essas escolhas não são confirmadas nem negadas pelo model card do Qwen. Elas são
compatíveis com os requisitos empíricos, especialmente estado integral + view
derivada, contenção mecânica no grafo e replay de eventos.

A comparação de painel usou o mesmo turno gravado nos três candidatos e encontrou
que `assistant-ui` expõe reasoning e nome/argumentos/resultado/estado das tools,
enquanto CopilotKit escondia esses elementos por default e introduzia processo
Node adicional. Dois erros de schema AG-UI foram observados no cliente real:
papel de reasoning incorreto e desfecho final enviado como string em vez de união
discriminada. Isso valida a necessidade de um replay bench, não mede impacto na
qualidade do modelo. [HERANÇA §11](../../HERANCA-PARA-O-2.0.md#11-painel-e-protocolo).

Primeiro artefato de UI deve ser o servidor de replay de conversa gravada, sem
modelo, rede ou custo. Eventos de ocupação de contexto e aviso de visão usam
`CUSTOM`; o restante segue AG-UI nativo. Reabrir framework, protocolo ou painel
exige nova medição, conforme a regra do dono do projeto.

## Decisões finais de portabilidade

Esta tabela foi a base normativa da reconciliação. O contrato vigente e mais
curto está em [`DECISOES-2.0.md`](../DECISOES-2.0.md). “Manter” preserva uma
decisão/regra; “substituir” marca recomendação anterior superada; “experimentar”
exige métrica antes de promoção; “não portar” proíbe trazer o mecanismo do 1.0.

| Disposição | Item | Decisão e gate |
|---|---|---|
| **MANTER** | Protocolo Qwen3.5 da revisão do checkpoint | Template oficial + parser compatível; manifesto com hashes e golden tests |
| **MANTER** | `presence_penalty=0` no perfil local de tools | Baseline de `mitos`/Ollama; revalidar em toda nova combinação |
| **MANTER** | `temperature=0.3` como controle histórico | Não chamar de ótimo; serve para reproduzir os números herdados |
| **MANTER** | `think:true` no braço de reprodução Ollama/`mitos` | É a configuração efetivamente medida; não generalizar a outros backends |
| **MANTER** | Contexto operacional de 24.576 na RX 7600 atual | Subir só após medir VRAM, spill, latência e OOM |
| **MANTER** | Prompt model-facing em inglês e saída em português | Preservar enquanto o A/B fatorial não isolar idioma/comprimento |
| **MANTER** | Nomes oficiais em inglês `snake_case` | Somente quando a semântica coincidir; registry é a fonte da lista |
| **MANTER** | Jaula de path, SSRF, erro honesto e consentimento explícito | Controles em código; prompt nunca é barreira de segurança |
| **MANTER** | Teto mecânico do turno, inicialmente 15 | No último passo, nenhuma tool; desfecho não pode ser sobrescrito |
| **MANTER** | Dedup, extração única de HTML, corte e calibrador | Rodam na view pré-modelo; histórico gravado permanece integral |
| **MANTER** | SQLite local sem conteúdo; LangSmith desligado | Backend remoto e telemetria externa somente por knob/consentimento explícito |
| **MANTER** | LangGraph + `SqliteSaver` + hook pré-modelo | Decisão fechada; medir custo e correção sem reabrir por preferência |
| **MANTER** | AG-UI + `CUSTOM` + `assistant-ui` | Começar pelo replay bench e validar schema no cliente real |
| **SUBSTITUIR** | Path absoluto nos schemas/exemplos | Expor path relativo ao workspace; resolver/canonicalizar no executor |
| **SUBSTITUIR** | Perfis gerais oficiais com `pp=1.5/2.0` como default de tools | Usá-los apenas como braços de controle; default local é 0 |
| **SUBSTITUIR** | Seleção dinâmica desde o primeiro release | Registry pequeno integral; roteador só após catálogo/métrica justificar |
| **SUBSTITUIR** | `run_shell_command` P0 | Adiar no baseline; criar tools tipadas e listar registry real nos erros; reavaliar só após gates de sandbox/policy |
| **SUBSTITUIR** | Verificação de página como tool | Verificação automática, no máximo uma vez por SHA, atrás de experimento |
| **SUBSTITUIR** | Guarda que apenas informa/retorna erro | Remover transição/tool ou terminar o grafo mecanicamente |
| **EXPERIMENTAR** | `mitos` versus checkpoint oficial fixado | Mesmos casos, template conhecido, quantização declarada e eval de paridade |
| **EXPERIMENTAR** | Temperaturas 0,3/0,6/0,7 | Fixar `pp=0` e todos os demais fatores; medir tarefa completa |
| **EXPERIMENTAR** | Thinking on/off | Sucesso, calls, parsing, latência e tokens; capability por backend |
| **EXPERIMENTAR** | Prompt PT/EN × longo/enxuto | Desmembrar idioma de tamanho e separar path de erro de sequência |
| **EXPERIMENTAR** | Nomes alternativos de tools | Manter descrição/schema constantes; medir seleção, argumentos, calls extras e sucesso fim a fim |
| **EXPERIMENTAR** | Teto 8 versus 15 | Sucesso, abandono, passos, repetição e latência nos casos reais que falharam |
| **EXPERIMENTAR** | Encerramento por repetição do mesmo alvo | Gate terminal real e desfecho próprio; as duas medições antigas são inválidas |
| **EXPERIMENTAR** | Verificação automática 1×/SHA | Comparar com 18,6 s sem verificação e 194 s com tool, além de validade do HTML |
| **EXPERIMENTAR** | Roteamento/tool search ao crescer além de ~8 tools | Avaliar catálogos 5/10/20/40; só implementar ao localizar degradação |
| **EXPERIMENTAR** | Resumo/recorte de prosa por relevância | Exigir economia e fidelidade; é mecanismo novo, não o compressor antigo |
| **NÃO PORTAR** | Qualquer linha de código do 1.0 | Rewrite total; portar conhecimento, métricas e invariantes |
| **NÃO PORTAR** | Compressor mecânico de prosa | 5.579 blocos e ganho arredondado a 0% |
| **NÃO PORTAR** | Compressor de saída de shell | Sem tool de shell e 3 blocos com ganho 0% |
| **NÃO PORTAR** | Aviso de repetição como contenção | 27% continuaram repetindo; manter apenas como telemetria se útil |
| **NÃO PORTAR** | Teto que devolve erro e mantém a ação disponível | 39 estouros e até 12 calls contra teto 5 |
| **NÃO PORTAR** | `think: true` literal universal | Backend sem suporte morre com 400 antes do primeiro token |
| **NÃO PORTAR** | Lista de tools escrita à mão no prompt | Derivar sempre do registry e da policy efetiva |
| **NÃO PORTAR** | Histórico com buracos ou view persistida como verdade | Checkpointer guarda registro integral; view é descartável |
| **NÃO PORTAR** | Cache de arquivo apenas por path | Conteúdo reescrito exige chave por SHA dos bytes |
| **NÃO PORTAR** | Fallback silencioso de backend/telemetria com conteúdo | Falhar no boot ou exigir consentimento; nunca exportar contexto por surpresa |

## Fontes primárias oficiais reutilizadas

- [Qwen/Qwen3.5-4B — model card, sampling, thinking, serving e benchmarks](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Qwen/Qwen3.5-4B — configuração do checkpoint](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/config.json)
- [Qwen/Qwen3.5-4B — tokenizer e template de chat/tools](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/tokenizer_config.json)
- [Qwen/Qwen3.5-4B — índice dos pesos](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/model.safetensors.index.json)
- [Qwen Code — catálogo de nomes canônicos](https://github.com/QwenLM/qwen-code/blob/main/packages/core/src/tools/tool-names.ts)
- [Qwen Code — introdução e segurança de tools](https://qwenlm.github.io/qwen-code-docs/en/developers/tools/introduction/)
- [Qwen Code — sandbox](https://qwenlm.github.io/qwen-code-docs/en/users/features/sandbox/)
- [Qwen-Agent — gestão de contexto](https://qwenlm.github.io/Qwen-Agent/en/guide/core_moduls/context/)
