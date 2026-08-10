# Herança para o harness 2.0

Dossiê de transferência. O 1.0 (Flask + Ollama, sem framework) está sendo aposentado; o 2.0 nasce em
LangChain/LangGraph com painel novo. **Nenhuma linha de código atravessa** — o que atravessa é o que
foi medido, o que custou caro descobrir, e o que já foi decidido para não ser reaberto.

Escrito em 2026-08-08, sobre ~150 turnos de bancada e 90 turnos de produção instrumentados.

## 1. O que este arquivo é, e o que não é

**É** o registro do que o 1.0 aprendeu com número ao lado: o que rendeu, o que não rendeu, quais
premissas estavam erradas e quanto cada erro custou.

**Não é** a spec do 2.0. Não escolhe suas ferramentas, não desenha seu grafo, não define seu módulo.
Quem constrói decide isso — com este dossiê na mão, para não pagar de novo o que já foi pago.

Regra de leitura: **todo número aqui foi medido nesta máquina, com este modelo.** Onde não foi, está
escrito "não provado". Nunca trate os dois igual — foi exatamente essa confusão que produziu o erro
mais caro do 1.0 (seção 7).

## 2. Decisões do 2.0 já tomadas

Fechadas em entrevista com o dono do projeto. Não são sugestões; são o ponto de partida. Reabrir
qualquer uma exige medição nova, não preferência.


| #  | decisão                                                                                                             | por quê                                                                                       |
| -- | -------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| 1  | **Nenhum código do 1.0 é portado**                                                                                 | rewrite total; o valor do 1.0 é o conhecimento, não a implementação                        |
| 2  | **Este dossiê é longo de propósito**                                                                              | quem constrói extrai o que virar regra permanente do 2.0                                      |
| 3  | **`qwen3.5 4b` local é o alvo**, backend remoto como knob desde o dia 1                                             | é o único modelo com ~150 turnos medidos; achado marcado "Qwen 4B" não vale para outro      |
| 4  | **Recusas em duas classes**: PROIBIDO vs SEM DEMANDA MEDIDA                                                          | seção 6 e 10; misturar as duas é como uma decisão sem evidência vira dogma                |
| 5  | **Verificação de página é automática**, pós-escrita, 1x por sha do conteúdo — o modelo não tem a ferramenta | o loop só existe porque o modelo pode pedir de novo (seção 5)                               |
| 6  | **SQLite local sem conteúdo é a fonte de verdade da métrica**; LangSmith atrás de knob desligado                 | o trace do LangSmith carrega prompt, resposta e conteúdo de arquivo para fora da máquina     |
| 7  | **Conjunto de ferramentas é novo**; os limiares desta seção 8 são referência calibrada, não especificação    |                                                                                                |
| 8  | **Estado no `SqliteSaver` do LangGraph**; a view vai derivada num hook pré-modelo                                   | paga a dívida do SQLite e preserva a regra do ADR-0001 (seção 6)                            |
| 9  | **AG-UI nativo**; telemetria própria do harness em evento `CUSTOM`                                                  | painel deixa de ser reescrita; ver seção 11                                                  |
| 10 | **ADR + relatório de medição**, sem spec numerada por feature                                                     | o que se pagou foi o registro da decisão e do número; 17 specs é cerimônia de produto      |
| 11 | **Português na documentação**                                                                                     | a regra "inglês para o que o modelo lê" vale para o prompt do loop, não para doc de projeto |

## 3. O modelo: `qwen3.5 4b` medido

Tudo nesta seção é **específico deste modelo e desta placa**. Se o 2.0 mudar de modelo, releia como
histórico, não como requisito.

### Máquina e teto real

```
AMD Radeon RX 7600, 7.98 GB VRAM   (não NVIDIA)
31 GB RAM, 12 núcleos
mitos:latest (qwen3.5 4b abliterado)  3.5 GB  100% GPU  ctx 24576  →  68 tok/s
```

O teto desta placa é **4B denso, ou 8B com contexto cortado**:

- `granite4.1:8b` — 5.35 GB de pesos viram **7.7 GB** com 24k de contexto, e **17% derrama para a
  CPU**. Roda, custa 40.7 s/turno contra 20.2 do 4B.
- `qwen3.5:9b` (6.59 GB) — não cabe de jeito nenhum.

### `presence_penalty` acima de zero faz o modelo abandonar a ferramenta

O achado de configuração mais importante do 1.0, e o menos óbvio.


|                                           | `pp=1.1`    | `pp=0`  |
| ----------------------------------------- | ----------- | ------- |
| turnos mudos (zero chamada de ferramenta) | **5 em 18** | 2 em 18 |
| chamadas de`write_file`                   | 5           | 9       |
| tamanho do arquivo entregue               | 2349 B      | 3921 B  |

**Mecanismo:** penalidade de presença pune token que já apareceu — e o conteúdo do arquivo vai dentro
do argumento da chamada de ferramenta. O modelo foge da ferramenta e despeja o texto na conversa, onde
a penalidade não dói. Turno mudo não é preguiça do modelo: é a amostragem punindo o caminho certo.

**Requisito: `presence_penalty=0`.** E cuidado com Modelfile: `jaahas/qwen3.5-uncensored:4b` traz
`presence_penalty 1.5` cozido de fábrica — pior que o valor tóxico medido. Quem manda o parâmetro
explicitamente sobrescreve; quem confia no default do modelo pega 1.5 sem saber.

`temperature=0.3` foi o valor usado em toda a medição. Baixa = escolha de ferramenta mais confiável.

### Raciocínio explícito não é universal

Modelo fora da linha Qwen responde `400 does not support thinking` e **morre antes do primeiro
token**. Isso prendeu o 1.0 a uma família de modelo por meses, sem estar escrito em lugar nenhum — era
um literal no payload. O 2.0 precisa de um knob desde o primeiro dia.

### O modelo desobedece o prompt entre 33% e 78% das vezes

Medido contra uma instrução específica do system prompt ("se você não tem a ferramenta de teste, diga
isso e pare"):


| braço                                | desobedece |
| ------------------------------------- | ---------- |
| `mitos` config corrigida              | 78%        |
| `jaahas-uncensored:4b`                | 78%        |
| `mitos` + frase extra                 | 60%        |
| `granite4.1:8b`                       | 44%        |
| **com a ferramenta de teste na mesa** | **0%**     |

**Nenhum modelo candidato conserta.** A lição não é sobre modelo: **o prompt orienta, quem garante é
o código.** No 1.0, a regra sobre URL tinha um validador atrás e nunca foi violada; a regra sobre
"diga que não pode testar" não tinha nada atrás e foi violada em 3 de 4 turnos.

Corolário para o 2.0: toda regra que importa precisa de mecanismo. Regra que só existe no prompt é
uma preferência, não uma garantia.

### Uma frase no prompt eliminou uma classe de erro

O prompt dizia só o caminho absoluto do workspace, e o schema do argumento `path` não tinha descrição.
O modelo tentava caminho absoluto, errava um pedaço, tomava erro de jaula e queimava passo.

Erros de caminho: **7 em 18 turnos** sem a frase, **0 em 18** com ela. Eliminação categórica, não
melhora de taxa. A frase dizia, em uma linha, que caminho é relativo ao workspace e que absoluto é
recusado.

### Prompt em inglês obedece mais

Mesmo modelo, mesma config, mudando **só** o idioma do system prompt (25 linhas PT contra 21 EN),
15 turnos cada:


|                                  | PT   | EN      |
| -------------------------------- | ---- | ------- |
| desobediência à regra medida   | 67%  | **40%** |
| respostas que saíram em inglês | 0%   | **0%**  |
| s/turno                          | 25.8 | 29.9    |

O risco era o modelo responder no idioma do prompt; uma última linha mandando responder em português
segurou em 14 de 14. Ganho de 27 pontos com n=15: **direção confiável, tamanho não.**

Duas ressalvas de método, sem as quais a tabela engana: a contagem de erro conflava erro de caminho
com erro de sequência (ler arquivo que ainda não existe), e o inglês veio junto de um enxugamento de 4
linhas — **idioma e tamanho não foram isolados um do outro.**

## 4. O que rendeu, com o número

Medido no banco de produção: 90 turnos, 415 montagens de contexto, 188 chamadas de ferramenta.


| peça                                                                                                                 | número                                                                                       |
| --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| **dedup por referência** — resultado de ferramenta repetido no histórico vira ponteiro para a primeira ocorrência | 1.264 blocos,**1.279.397 → 19.933 tokens (−98%)**                                           |
| **extração de texto de HTML** — remove `nav`, `footer`, `aside`, `script`, `style`, preserva quebras               | 56 blocos, 36.723 → 3.853 tokens (**−90%**)                                                 |
| **corte por orçamento** — descarta as mensagens mais antigas até caber na janela                                   | **141 de 415 montagens (34%)** precisaram cortar. Sem isso, um terço estoura                 |
| **compressão de código**                                                                                            | 99 blocos, −28%                                                                              |
| **calibrador de contagem de token**                                                                                   | 399 amostras, erro relativo**1,8%**; aprendeu 4.0 → **9.74** tokens de overhead por mensagem |
| **instrumentação**                                                                                                  | é a razão deste dossiê existir                                                             |

Quatro coisas a herdar como **requisito**, não como sugestão:

1. **Dedup é o maior ganho do sistema inteiro**, por duas ordens de grandeza. Um turno que baixa a
   mesma página três vezes paga o texto uma vez. No 2.0 isso vive no hook pré-modelo.
2. **A extração de HTML mora na camada de contexto, não na ferramenta.** No 1.0 existiam duas
   respostas para "o que sai de uma página", e a pior rodava em produção. Uma função só, um lugar só.
3. **O orçamento é a janela do modelo — um número só.** No 1.0, o orçamento de compressão *era* o
   `num_ctx` enviado ao Ollama, sem chance de divergir. Dois números divergem em silêncio.
4. **A contagem de token precisa de calibração empírica.** O tokenizer local não vê o overhead do
   template de chat nem o schema das ferramentas. O 1.0 aprendia esse overhead da contagem real que o
   servidor devolve, com média móvel persistida em disco: 1,8% de erro contra um palpite inicial que
   errava por mais que o dobro (4.0 contra 9.74 reais). **Calibração é por backend** — o overhead do
   template do Ollama não vale para uma API diferente.

## 5. O que não rendeu — não porte

Igualmente importante, e mais difícil de admitir.


| peça                                                  | o número que a condena                                                                                                |
| ------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| **compressão de prosa**                               | 5.579 blocos, 4.818.608 → 4.797.226 tokens =**−0%**. É o maior volume do sistema e não economiza nada              |
| **compressão de saída de shell**                     | 3 blocos, −0% — e o 1.0 não tem ferramenta de shell                                                                 |
| **anunciar chamada repetida ao modelo**                | **27% das chamadas continuaram sendo repetição idêntica**, pior caso **7 vezes** no mesmo turno                     |
| **teto de chamadas ao mesmo alvo (como implementado)** | estourou**39 vezes** num braço de 11 turnos; o contador chegou a **12** chamadas no mesmo alvo depois de um teto de 5 |
| **verificação de página como ferramenta do modelo** | 18.6 →**194 s/turno**, 61% de repetição, **6 de 12 turnos** morrendo no limite                                      |

### Por que a compressão de prosa falhou, e o que fazer diferente

Um compressor de texto que só sabe remover espaço, linha repetida e bloco duplicado não comprime
prosa — prosa não repete. Reduzir página grande depende de **escolher** quais trechos manter, que é
problema diferente e não foi resolvido. Se o 2.0 tentar, tente pelo caminho da escolha (sumarização,
recorte por relevância), não pelo da remoção.

### Por que os três guardas de loop falharam

O padrão é o mesmo nos três: **o guarda informa o modelo e espera que ele mude de comportamento.**

- avisar "você já chamou isso" → repetiu de novo, 27% das vezes;
- devolver erro no teto do mesmo alvo → **erro é coisa que o modelo retenta**. Trinta e nove avisos de
  "insistir não vai mudar", e ele insistiu;
- pedir no prompt para parar → desobediência entre 33% e 78%.

**Regra para o 2.0: guarda que depende da cooperação do modelo não é guarda.** Contenção precisa ser
mecânica — o harness deixa de oferecer a ação, ou encerra o turno. Contar e reclamar é telemetria
disfarçada de controle.

### O caso central: o loop que não converge

O ciclo medido, reproduzido de duas sessões reais de produção:

```
write_file (HTML)  →  verificar página  →  erro de JavaScript persiste
      ↑                                              │
      └──────────────────  reescreve  ←──────────────┘
```

Até bater o limite de passos. Os erros eram sempre os mesmos dois tipos (sintaxe inválida, função não
definida), e o modelo não convergia.

Ao mesmo tempo, a verificação de página é **a única coisa medida que conserta a obediência ao
prompt** — de 60% de turnos desobedientes para zero, porque com a ferramenta na mesa o modelo a usa
(9 de 12 turnos) em vez de afirmar que a página funciona sem ter como saber.

Esse dilema é a herança mais valiosa deste dossiê, e a decisão nº 5 é a saída escolhida:

> **A verificação é automática e do harness, não do modelo.** Depois de cada escrita de HTML, o
> harness testa a página e injeta o resultado uma vez. O teto é **por sha do conteúdo**, não por
> chamada: mesma versão do arquivo nunca é testada duas vezes. O modelo não tem a ferramenta, então
> não pode pedir de novo — e o loop deixa de ser possível em vez de ser desencorajado.

Não foi medido. É hipótese, e o 2.0 deve tratá-la como tal: instrumente e compare com a linha de base
deste dossiê (18.6 s/turno sem verificação, 194 com).

## 6. Invariantes que não dependem de framework

Estes não são achados de medição — são as coisas cujo bug custa arquivo perdido, rede interna exposta
ou registro falsificado. Todos sobrevivem à troca de framework, e nenhum vem de graça no LangChain.

### PROIBIDO (quebrar custa dado ou segurança)

**Jaula de caminho.** Toda ferramenta de arquivo resolve o caminho **seguindo symlink** e exige que o
resultado esteja dentro do diretório de trabalho. Nunca construa um caminho a partir de argumento do
modelo sem passar por essa checagem. Limpeza de workspace checa se é symlink antes de apagar
recursivamente — senão apaga o alvo do link, fora da jaula.

⚠️ A jaula decide **qual arquivo abre**. Ela nunca prometeu nada sobre para onde o conteúdo vai
depois: com backend de modelo remoto, o que ela deixou ler sai da máquina no passo seguinte. Limite,
não furo — mas escreva isso onde o próximo leitor veja.

**Bloqueio de rede interna (SSRF).** Resolver o DNS e barrar loopback, privado, link-local, reservado
e multicast, além de qualquer esquema fora de `http`/`https`. É o que impede o agente de alcançar o
próprio servidor de modelo. **Revalidar a cada redirect** — uma página escapa respondendo `302` para
`127.0.0.1`, e no 1.0 isso significou que toda saída HTTP passava por um único opener com o
revalidador instalado. Limite conhecido e deliberado: **uma resolução só, vulnerável a DNS
rebinding** (o intervalo entre a checagem e a conexão real).

**Ferramenta honesta.** Bloqueio nunca se disfarça de vazio; falha nunca se disfarça de sucesso. A
busca do 1.0 distinguia três casos — resultado, "não achei nada", e "o provedor me barrou" — e o
terceiro voltava marcado como retentável. Ferramenta que engole exceção e devolve resultado plausível
quebra isso, e **é exatamente o comportamento default das tools prontas de ecossistema**: devolvem
string vazia onde o desfecho era um bloqueio.

Sub-regra medida: **erro deve dizer o que existe.** O modelo inventou uma ferramenta de shell **14
vezes** em produção contra um erro mudo. Quando o erro passou a listar as ferramentas existentes, a
invenção virou correção no passo seguinte.

**Sem ferramenta de shell.** Deliberado: jaula de caminho não segura shell. O ecossistema LangChain
tem uma pronta; a pressão para adotá-la é real (as 14 tentativas acima). Não adote.

**Histórico não tem buraco.** Dentro de uma conversa viva nada é removido. Descarte só alcança
conversa inteira, por idade. Histórico com buraco é pior que histórico ausente, porque ninguém
percebe. Isso inclui a tentativa ruim: quando uma resposta é rejeitada por validação, ela **fica** no
histórico — o modelo precisa ver o que escreveu.

**A view é derivada; o histórico é registro.** Compressão, dedup e corte por orçamento produzem uma
lista nova que vai ao modelo. O estado gravado nunca é tocado. No 2.0 isso significa: o checkpointer
guarda a verdade, o hook pré-modelo produz o descartável.

**Instrumentação nunca grava conteúdo.** Endereço, tamanho, tempo, desfecho. Corpo de página e
conteúdo de arquivo vivem na sessão. E ela **nunca derruba o turno**: falha de escrita avisa uma vez e
se desliga, em vez de fingir que coleta.

### SEM DEMANDA MEDIDA (pode entrar, mas com métrica na frente)

Não são proibidos. Foram recusados por falta de demanda medida, e a doc do LangChain vai sugerir os
três na primeira página:

- **RAG e embeddings** — fora de escopo desde o começo, sem nenhum caso medido que os pedisse;
- **seleção dinâmica de ferramentas** — com 8 ferramentas, escolher quais oferecer é otimização sem
  problema. Vira relevante em outra ordem de grandeza;
- **retenção parcial de sessão** — recusada pela forma (ver "histórico não tem buraco"), não pela
  ideia de retenção. Retenção por idade de conversa inteira segue desejável e nunca foi implementada.

Regra: entrar exige um número que justifique, não uma seção de documentação que sugira.

## 7. As premissas erradas, e o que cada uma custou

Esta seção é a mais útil do dossiê. Todas são erros reais, encontrados depois de virarem decisão.

**`nvidia-smi: não instalado` fechou a porta do modelo de visão local — numa máquina com placa AMD.**
`nvidia-smi` é utilitário da NVIDIA e é cego para uma Radeon. O modelo do loop **sempre rodou 100% em
GPU** enquanto a decisão arquitetural registrada dizia "CPU pura, seria lento demais". Custou meses de
uma porta fechada por engano, e a decisão precisou de um segundo documento para ser revogada.
**A checagem certa é perguntar ao próprio serviço o que ele está fazendo**, não a um utilitário de
outro fabricante o que ele acha que existe. *Plausível não é medido.*

**Raciocínio fixo no payload.** Um literal `think: true` prendeu o harness a uma família de modelo.
Qualquer outro morria com `400` antes do primeiro token. Ninguém decidiu isso; foi construído.
**Procure no 2.0 o que está fixo no payload e não deveria estar.**

**Geração recusada matava o turno.** Quando o servidor recusava uma geração malformada, a exceção
subia sem tratamento e o turno morria mudo. 1 em ~130 turnos — raro e fatal. A correção: recusa de
formato vira erro **no histórico**, o modelo corrige; recusa que insiste (2 vezes) sobe, porque aí não
é geração torta, é modelo errado ou config quebrada. **Distinga "o modelo gerou torto" de "a config
está quebrada"** — a segunda não se resolve com retentativa.

**Um campo sobrescrito tornou um mecanismo invisível na métrica.** O desfecho do turno era marcado
como "teto atingido" e depois sobrescrito por "completo". Resultado: duas tentativas de medir o
mecanismo de contenção falharam, e ninguém sabia por quê. **Se a métrica não distingue o desfecho, a
medição não existe** — e você vai passar horas medindo o nada.

**Chave de cache sem o conteúdo.** O cache da ferramenta de visão usava o caminho do arquivo. Mas o
screenshot é reescrito no mesmo caminho a cada verificação, então a segunda pergunta recebia **a
descrição da imagem anterior, em silêncio**. A chave passou a incluir o sha dos bytes. **Cache de
arquivo que o próprio loop reescreve tem que ser por conteúdo.**

**Caminho de workspace longo e estranho contaminou uma bancada inteira.** Rodar os braços com o
workspace num diretório temporário de caminho longo fez o modelo errar o caminho nos **dois** braços,
matando o A/B. Custou 20 minutos até a causa aparecer. **Bancada usa caminho curto e comum** — a
variável em teste é a que você mudou, não a que veio de brinde.

**Um elemento vazio de HTML matou um compressor por meses.** O extrator tratava `<meta>`, `<link>` e
`<img>` como abertura de bloco a pular, e por isso devolvia só o título da página. O compressor de
HTML parecia funcionar e não fazia nada. **Compressor que não pode falhar alto precisa de teste com
número, não de inspeção visual.**

**Registro de backend guardando o objeto em vez do nome.** Um dicionário que mapeava nome → função
congelava a referência no import. O dublê de teste que trocava a função passava batido, e a suíte que
prometia não tocar na rede foi bater na API real. **Registro guarda o nome; resolva na chamada.**

## 8. Ferramentas: comportamento medido e limiares calibrados

O 2.0 escolhe suas ferramentas. Isto é referência do que já foi calibrado.

### As 8 do 1.0

Ler arquivo, escrever arquivo, listar diretório, apagar arquivo, buscar na web, baixar página,
verificar página (navegador), descrever imagem (modelo de visão). Duas eram condicionais — existiam
só se a config as ligasse. **Prompt nunca lista ferramenta na mão:** a lista vem do registro real, e
prompt que esconde ferramenta ligada (ou cita ferramenta desligada) ensina o modelo a duvidar do
prompt.

### Baixar página: quando o navegador é necessário

Medido em produção, por domínio:


| caso                                                                        | resultado                                                            |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Wikipédia                                                                  | **160.820 caracteres** por HTTP simples, navegador nunca necessário |
| sites com conteúdo montado por JavaScript (loja de extensão, agregadores) | **100%** exigiram navegador (3/3, 3/3, 4/4)                          |
| um domínio grande de galeria                                               | 12 de 30 exigiram — a mesma origem oscila                           |

Duas lições: a decisão **não pode ser do modelo** (ele não sabe), e lista fixa de domínios acerta os
casos gritantes mas não cobre o oscilante. O 1.0 escalava por sintoma — HTTP simples primeiro, e
navegador quando o texto extraído era pequeno demais ou continha marcador de "ative o JavaScript".
A lista fixa continuou como dívida aberta.

**Limiar de página vazia: 120 caracteres.** Calibrado para pegar "este vídeo não está disponível"
(91 caracteres) sem derrubar página curta legítima (~180). Página que carrega e extrai menos que isso
é **erro**, não sucesso — com o pouco que veio incluído na mensagem, porque "vídeo indisponível" é
justamente a informação que o modelo precisa. Instrumento cego de propósito: pega o caso gritante, não
julga se a página respondeu à pergunta.

**Escada de espera entre tentativas: 5, 10, 20, 40, 80 segundos** (5 tentativas). Página que não
terminou de carregar merece nova tentativa; página que recusou, não.

**Cache por turno**, morrendo com ele: argumento idêntico devolve o resultado já obtido, sem ir à
rede. A chave da busca ignora caixa e espaçamento; a da URL é exata.

**Teto de passos do turno: 15.** No passo do limite, o harness manda o modelo responder com o que tem
**e não oferece ferramenta nenhuma** — oferecer ferramenta e pedir resposta final ao mesmo tempo é
convite a não responder, e o turno acabava sem resposta.

### Descrever imagem: o resultado é palpite de outro modelo

Única ferramenta cujo resultado **não é medição do harness**. Duas consequências obrigatórias:

1. o resultado entra no histórico **prefixado** com um aviso de que é descrição gerada por modelo de
   visão e pode errar;
2. como o modelo descarta a etiqueta ao redigir, o harness prefixa a **resposta final** com um aviso
   próprio quando a visão foi usada no turno — anotação do harness, fora do histórico, não palavra do
   modelo.

Custo medido da versão local: um PNG com `IMC 24.7` virou `IMC 247`. Entre os dois mora a diferença
entre um resultado normal e um número impossível, e ninguém a jusante tem como saber que o ponto
existiu. **Privacidade por precisão** — o modelo pequeno erra mais em texto miúdo, que é justamente o
caso do screenshot.

### Consentimento é explícito e barulhento

Sempre que um dado sai da máquina, o 1.0 exigia um ato: chave de API escrita à mão no arquivo de
config. Sem chave, **a ferramenta não existe** — não fica quebrada, não avisa depois. E nome de backend
errado **derruba o processo no boot**, nunca cai em fallback: fallback silencioso manda o dado
justamente para o lugar que a pessoa achou que tinha desligado.

O 2.0 herda isso com escopo maior: com backend de modelo remoto, **todo o contexto sai da máquina em
todo passo** — prompt, conversa, conteúdo de arquivo lido, texto de página. Consentimento por item
deixa de existir; quem liga está exportando o trabalho, não uma amostra dele.

### Backend pago: o que já foi aprendido

O 1.0 ganhou um backend remoto nos últimos dias. O que vale levar:

- **erro passa a ter preço.** Falta de saldo, chave inválida e limite de taxa **não** são geração
  malformada: precisam subir imediatamente. Retentar duas vezes queima passos e faz a pessoa ler
  "geração recusada" quando a resposta certa é "acabou o crédito";
- **contagem de token para o custo é evento próprio** — entrada, cache hit, cache miss, saída,
  raciocínio. Grave o token, não o dólar: preço muda e a própria doc do fornecedor avisa que vai subir;
- **raciocínio é cobrado como saída**, e o default do fornecedor é o nível mais caro. Esse knob tem que
  estar visível onde a pessoa vê o resultado, não escondido no arquivo de config;
- **janela grande muda o teto de "quanto cabe" de VRAM para conta.** Um contexto de 1M convida a
  gastar;
- **o tokenizer local é de outra família** e a estimativa fica torta — quem corrige é o calibrador, com
  a contagem real que a API devolve, e **em arquivo separado por backend**.

## 9. Como medir no 2.0

Sem isto, o 2.0 chega em três meses sem nenhum número — e este dossiê inteiro existe porque o 1.0 não
estava nessa situação.

### O que gravar por evento

Um registro por acontecimento, com identificador de turno e de passo em todos. As classes que se
pagaram:


| evento                    | campos que serviram                                                                                                                                                                                  |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **chamada de ferramenta** | nome, argumentos, deu certo, era retentativa, número da tentativa, tamanho do resultado, classe do erro, se era repetição (e a qual repetição), alvo, quantas vezes naquele alvo, milissegundos |
| **montagem de contexto**  | tokens estimados, orçamento, quantas mensagens, quantas descartadas, e por bloco: tipo, tokens antes, tokens depois, milissegundos                                                                  |
| **calibragem**            | estimado, real, número de mensagens, overhead antes e depois, amostras                                                                                                                              |
| **turno**                 | passos, limite,**desfecho** (completo, limite, teto, parado, rejeitado, erro, abandonado), quantas chamadas por ferramenta                                                                           |
| **rede**                  | domínio, motor usado (HTTP simples ou navegador), caracteres por motor, se foi forçado                                                                                                             |
| **uso/custo**             | backend, modelo, tokens de entrada, cache hit, cache miss, saída, raciocínio                                                                                                                       |

O campo de **desfecho** é o mais importante e o mais fácil de estragar: grave-o no encerramento
garantido do turno, inclusive quando ele morre. Registro que só grava quando dá certo não é registro —
e o turno que morre é justamente o que interessa medir.

### As perguntas que o leitor de métrica precisa responder

Se o schema não responde uma destas, o schema está errado:

1. quais domínios exigiram navegador, e quanto texto cada motor trouxe;
2. quantos turnos bateram o limite de passos, e em qual ferramenta os passos foram gastos;
3. quanto a estimativa de token erra contra a contagem real;
4. quanto a compressão economiza em uso real, por tipo de bloco;
5. quantas chamadas foram repetição idêntica, e qual o pior caso;
6. quantos tokens e quanto dinheiro por modelo.

Domínio que vai direto para o navegador precisa sair da pergunta 1 — ele nunca foi testado no motor
simples e provaria a si mesmo.

### Como rodar bancada sem contaminar

- **workspace, sessões e banco próprios** por braço, nunca os de verdade;
- **caminho curto e comum** para o workspace (ver seção 7);
- config por variável de ambiente, sem tocar no arquivo de config;
- **tarefa inventada não reproduz falha.** A primeira rodada usou tarefas fáceis e todos os braços
  passaram limpo. Foi preciso ir aos históricos reais achar o que quebrava. Bancada nasce de sessão de
  produção que falhou;
- mesmas tarefas, na mesma ordem, na mesma sessão, em todos os braços;
- **n é pequeno.** Com 9–15 turnos por braço, diferença de 1 erro não é diferença. Diga a direção e
  admita o tamanho.

⚠️ Teste que exercita o loop precisa da instrumentação desligada ou apontada para banco temporário,
senão suja o banco de verdade — e um banco no `.gitignore` esconde isso de um `git status`.

## 10. O que ficou aberto, e o que não ficou provado

Herança honesta inclui o que não se sabe.

**Não provado:**

- **encerrar o turno quando o teto do mesmo alvo estoura.** Duas tentativas de medir falharam: numa a
  patologia mal apareceu (o teto disparou 2x contra 20x do controle, e a queda de latência veio de
  turnos que não fizeram nada), na outra o processo caiu pelo bug da geração recusada. Continuou
  desligado por default por falta de medição válida;
- **a frase de prompt sobre a ferramenta de baixar página.** Zero erro em 15 turnos, mas o erro que
  ela mira acontecia 2 vezes em 33 — ver zero em 15 tem ~17% de chance de ser sorte. Conserto barato,
  não conserto provado;
- **recusa de conteúdo.** Nenhum dos nove braços tocou nisso: todos rodaram tarefa de arquivo, que não
  provoca filtro. É a dimensão que motivou usar um modelo abliterado, e está inteira em aberto;
- **a verificação automática de página** (decisão nº 5). É a hipótese central do 2.0 e não tem número.

**Aberto, com desenho decidido e código nenhum:**

- **memória de fato operacional.** Duas decisões arquiteturais registradas — só fato operacional
  (afirmação sobre ferramenta ou rede sustentada por medição do próprio harness, nunca conclusão do
  modelo), e evento é matéria-prima, memória é materializada. Nunca implementada. Vale reperguntar se é
  necessária: o gargalo medido nunca foi falta de memória;
- **retenção por idade** de conversa inteira;
- **substituir a lista fixa de domínios que vão direto ao navegador** por decisão medida;
- **catálogo de valores externos.** O 1.0 impedia o modelo de escrever URL: ele escrevia uma
  referência opaca e o harness trocava pela URL real na saída, validando byte a byte contra o que as
  ferramentas devolveram; resposta com link não sustentado era **descartada inteira**, nunca corrigida.
  Custou um fluxo de validação e uma retentativa própria, fora do teto de passos. Em produção:
  **1 rejeição em 90 turnos.** Não se sabe se o modelo parou de inventar URL ou se os turnos não
  pediram link. Código testado e quase não exercitado — só vale no 2.0 se citar link para fora for
  requisito.

## 11. Painel e protocolo

Decidido e verificado nos últimos dias, com painel real rodando.

**Escolha: `assistant-ui`** (TypeScript/React, MIT). O que a comparação mostrou, com três candidatos
consumindo o mesmo turno gravado:


|                                | assistant-ui                            | CopilotKit                    | inspector cru |
| ------------------------------ | --------------------------------------- | ----------------------------- | ------------- |
| dependências                  | 3 pacotes                               | 4 + runtime Node              | 0             |
| servidor no meio               | não                                    | **sim**, porta própria       | não          |
| raciocínio visível           | sim                                     | **não**                      | evento cru    |
| chamada de ferramenta visível | **nome, argumentos, resultado, estado** | **não** — só o texto final | evento cru    |
| estilo                         | seu, sem tema para brigar               | tema dele                     | seu           |

`assistant-ui` não manda **nenhum** CSS: exporta primitivos, e a aparência inteira é um arquivo seu.
Componente por ferramenta é uma entrada num registro (`tools.by_name`), não um `if`. Foi por isso que
ganhou: num harness de ferramentas, o que interessa na tela é justamente o que o CopilotKit esconde
por default. O CopilotKit sobe com 25 linhas, mostra menos, cobra um processo a mais — e vem com
telemetria anônima **ligada por default**.

Gosto visual aprovado, depois de quatro rodadas: **branco fixo** (com `color-scheme: light`, senão o
sistema em tema escuro pinta os controles nativos de cinza dentro de uma página branca), arredondado e
arejado, pergunta em bolha escura à direita, resposta como prosa corrida, input a ~50% da largura com
raio grande e botão circular. Rejeitados explicitamente: escuro de rascunho e a versão densa tipo IDE.

**Protocolo: AG-UI**, nativo desde o dia 1. Dois eventos do 1.0 não têm equivalente no padrão
(ocupação da janela de contexto e o aviso de visão na resposta final) e vão em evento `CUSTOM` — a
parte padrão segue padrão.

⚠️ **Dois desvios de schema que só um painel real pegou**, e que nenhum autoteste do lado do servidor
pegaria, porque quem valida o schema é o cliente:

1. o evento que abre o raciocínio exige papel `reasoning`, não `assistant`. Com o valor errado, o
   cliente recusa a execução inteira com erro de validação e o painel fica vazio sem dizer por quê;
2. o campo de desfecho do evento final é união discriminada por tipo, não string — mandar `"success"`
   derruba a execução.

**Ferramenta de bancada de painel:** um servidor de ~200 linhas que lê uma conversa gravada e a serve
como agente AG-UI, com ritmo configurável entre os deltas. Todo painel candidato recebe o mesmo turno,
com os mesmos tempos, **sem modelo ligado, sem token pago e sem rede** — inclusive os turnos feios,
com erro de ferramenta e chamada repetida. Vale reconstruir no 2.0 no primeiro dia em que houver
formato de evento: turno que já aconteceu é o melhor caso de teste que existe, e comparar painel deixa
de significar comparar backend.

## 12. As cinco frases que resumem tudo

Se o agente do 2.0 só levar cinco coisas deste arquivo:

1. **O prompt orienta; quem garante é o código.** Regra sem mecanismo atrás é preferência, e foi
   violada em 3 de 4 turnos.
2. **Guarda que depende da cooperação do modelo não é guarda.** Contar e reclamar não conteve nada —
   27% de repetição, 39 tetos estourados, 12 chamadas no mesmo alvo.
3. **Plausível não é medido.** Um utilitário da NVIDIA numa placa AMD fechou a porta certa por meses.
4. **Ferramenta honesta antes de qualquer coisa:** bloqueio não é vazio, falha não é sucesso. É o que
   as bibliotecas prontas quebram por default.
5. **Sem instrumentação, nada acima seria sabido.** Ela é a primeira coisa a construir, não a última.
