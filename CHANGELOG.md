# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [Não publicado]

### Adicionado

- **Recuperação por Corpus (RAG).** O Operator monta acervos por upload ou por
  coleta web, escolhe qual está ativo em cada Conversation e o harness injeta as
  passagens antes do primeiro AgentStep, com `corpus_search` disponível para o
  modelo refinar a busca depois. Cada Corpus é um SQLite próprio em
  `state_dir/corpora/`; apagar o Corpus apaga o arquivo, e a retenção global não
  o alcança. Autorização é efeito novo `corpus_read` com `CorpusGrant` cujo
  escopo nomeia o acervo — selecionar é conceder, "Desligado" é revogar. Chunk
  vindo do scraper carrega `UntrustedWebTaint`, então material da web continua
  custando confirmação de `data_egress`
  ([ADR-0011](docs/adr/0011-corpus-retrieval-and-corpus-grant.md)).
- **Busca híbrida com piso de relevância.** Vizinhança densa (`bge-m3`, 1024
  dimensões, via `sqlite-vec`) e BM25 (FTS5) fundidas por rank recíproco. O piso
  é lido na similaridade cosseno e não no escore de fusão — rank recíproco ordena
  e não mede, e o primeiro colocado pontua igual respondendo ou não à pergunta.
  Nada acima do piso é `empty`, com a instrução de dizer que não sabe.
- **Tradução só na query.** Documento é indexado como foi extraído; uma geração
  curta produz a versão autônoma em inglês que alimenta só a perna lexical,
  enquanto a densa usa o texto do Operator. Medição local: a pergunta crua em
  pt-BR na perna lexical contra corpus em inglês piora o ranking.
- **Coleta por API quando o site tem uma.** Semente com `/api.php` é coletada
  pela API do MediaWiki — wiki inteira, texto puro, sem seguir link; o resto cai
  num crawl com teto que respeita `robots.txt`. Job em segundo plano com
  progresso, cancelamento e retomada: a página que o Corpus já tem sai pelo
  endereço na listagem, antes de virar requisição, então uma wiki maior que o
  teto é coletada em rodadas sucessivas em vez de recomeçar do alfabeto. O
  extrato de artigo inteiro vem um por requisição porque é o que o MediaWiki
  concede — pedir vinte devolve dezenove páginas vazias.
- **Piso de tamanho de Chunk.** Seção mais curta que `chunk_minimum_tokens` não
  é indexada como passagem; o texto continua no Document. Medido numa wiki de
  jogo: abaixo de oito tokens a faixa é lista de links e andaime de citação, que
  vencia a busca lexical por casar com a pergunta ao pé da letra e ocupava uma
  das seis vagas do Turn sem responder nada. É onde começa o fato de uma linha.
- **Modo yolo.** Decisão permanente do Operator, global e desligada de fábrica,
  com opt-out por Conversation: enquanto estiver ligada o gate aprova toda
  confirmação — inclusive escrita sob `UntrustedWebTaint` — e concede WriteGrant
  e WebAccessGrant que faltarem. Cada chamada continua registrada como `waived`,
  nunca como `approved`. O risco aceito está em
  [`docs/adr/0008-operator-yolo-mode.md`](docs/adr/0008-operator-yolo-mode.md).
- **Tools `calculate` e `get_weather`.** A primeira estreia o efeito
  `pure_compute`, que não exige grant algum, e avalia a expressão por AST com
  whitelist — sem `eval`, com teto de expoente. A segunda usa Open-Meteo, que não
  pede credencial, como `data_egress` com `UntrustedWebTaint`.
- **Aba Configurações editável.** `PUT /api/admin/host-config` valida pelo mesmo
  caminho do setup, grava `host.json` atomicamente e devolve `restart_required`.

### Alterado

- **A busca deixou de ser paga.** A Brave Search API saiu inteira, junto com sua
  credencial e a plumbing dela. Entra SearXNG declarado pelo Operator, com
  DuckDuckGo sem chave como fallback; o endpoint declarado é liberado no
  `EgressGuard` como allowlist de um item, só no caminho de `web_search`
  ([ADR-0009](docs/adr/0009-search-provider-searxng-with-fallback.md)). O
  navegador local fica, e passa a aceitar qualquer Chromium.
- **O que vai para o modelo é XML**, não JSON minificado: ToolResult, automação
  interna e tentativa rejeitada são renderizados por
  `context_builder._xml_text`, determinístico porque o hash de deduplicação é
  calculado sobre ele. Schemas de tool seguem em JSON Schema no tool-calling
  nativo ([ADR-0010](docs/adr/0010-model-facing-messages-are-xml.md)).
- **Grant que falta vira diálogo, não fim de Turn.** `web_access_grant_required`
  passa a ser perguntado no meio do Turn como já acontecia com
  `write_grant_required`: aprovar concede o grant e o mesmo Turn continua pelo
  SSE aberto, sem reenviar o prompt. `workspace_root_grant_required` segue
  terminal, apontando para as Configurações.
- **Roteamento tool → executor por efeito**, não por prefixo de nome: quem
  declara `data_egress` vai para o executor web, o resto para o local.
- **Menu lateral só com navegação**: o logo e o avatar "OP" saíram.

### Removido

- **`.env` e todas as variáveis `HARNESS_*`.** `host.json` é a fonte única, e o
  que precisa existir antes do app virou flag: `--host`, `--port`,
  `--host-config` e `--setup`.

## [1.0.0] — 2026-08-10

Primeira release. O que ela exclui deliberadamente está em
[`docs/RELEASE-PENDING.md`](docs/RELEASE-PENDING.md).

### Adicionado

- **Confirmação de escrita com UntrustedWebTaint.** Um Turn que lê a web e pede
  para escrever agora para numa `ConfirmationGate` e espera a decisão do
  Operator, em vez de terminar em `blocked`. A decisão vale para aquela chamada:
  não cria grant, não persiste e não amplia autoridade. Registrada em
  CanonicalHistory como automação `write_confirmation`.
- **Autenticação do Operator.** A exposição de rede é derivada da credencial:
  sem senha, só loopback direto; com senha, toda rota exige sessão. Senha em
  PBKDF2-HMAC-SHA256 com 600.000 iterações, sessões opacas só em memória.
  Modelo de ameaça em [`docs/THREAT-MODEL-AUTH.md`](docs/THREAT-MODEL-AUTH.md).
- **Navegador Brave real**, dirigido por CDP sobre o `aiohttp` que já era
  dependência. Um processo e um `--user-data-dir` descartável por operação, e o
  destino é fixado com `--host-resolver-rules` a partir do endereço que o
  `EgressGuard` aprovou — o navegador não resolve DNS por conta própria.
- **Tiers de eval que faltavam.** `ModelCaseRunner` roda o AgentEngine real
  contra o RuntimeProfile e `BrowserBenchCaseRunner` replica a escalação contra
  uma bancada loopback determinística. Antes, `EvalService` bloqueava qualquer
  experimento com `runner_not_configured`.
- **Telas de setup e de login** no frontend; a primeira execução e o primeiro
  acesso deixaram de exigir `curl`.
- Ponto de entrada `harness`, README, CI, `scripts/run-experiment.py` e
  `scripts/validate-contracts.mjs --write`.

### Evidência

- `guarded_web_brave_escalation`, o único experimento
  `required_before_release`, rodou piloto de 15 casos por braço e promoção de 50
  por braço com três seeds, contra o perfil `mitos` e o Brave instalado. Ambos
  os braços passaram todos os casos, com zero violações de segurança, e o gate
  de promoção retornou `promoted`. Resultado e os sete digests congelados em
  `evals/experiments.json#results`.

### Corrigido

- `web_fetch` escalava para o navegador apenas em HTTP 401/403. Uma extração
  abaixo do limiar calibrado de 120 caracteres — o sintoma que
  `harness.json#network.web_fetch` declara, e exatamente como uma página
  renderizada por JavaScript se apresenta a um cliente HTTP — devolvia
  `empty_extraction` e desistia.
- O contador de violações de segurança marcava toda recusa do harness como
  violação, o que tornava o gate `zero_violations` impossível de passar por
  construção. Violação agora significa expectativa de segurança não cumprida.
- `config/harness.json` declarava `LangGraph SqliteSaver` e `assistant-ui`;
  nenhum dos dois existe no projeto.
- `CredentialStore` sobrescrevia uma credencial ao gravar a outra.
- O encerramento do navegador matava só o líder do grupo, deixando zygotes e o
  crashpad handler órfãos.
- `[tool.pyright]` não apontava para o `.venv`, e um checkout novo reportava
  1229 erros falsos.

### Limitações aceitas

Ollama local como único runtime, verificação de página desligada, um único
Operator sem papéis, sessão sem persistência, exceção de bancada restrita aos
tiers de eval, Python 3.13 em Linux x86_64 e UX web-first. Cada uma com seu gate
de saída em `docs/RELEASE-PENDING.md`.
