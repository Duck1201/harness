# Harness 2.0

Harness de agente LLM local, web-first, com policy por efeito. O modelo é tratado
como não confiável: seleção de tool, argumentos e resultados passam por validação,
autorização, confirmação e sandbox do harness. Autorização é resolvida pelo efeito
declarado (`workspace_read`, `workspace_write`, `data_egress`), nunca pelo nome da
tool.

Backend FastAPI + SQLite, frontend React servido pelo próprio backend, modelo local
via Ollama. O contrato normativo está em [`docs/DECISOES-2.0.md`](docs/DECISOES-2.0.md);
o que deliberadamente não entra na primeira release está em
[`docs/RELEASE-PENDING.md`](docs/RELEASE-PENDING.md).

## Requisitos

| Componente | Versão | Observação |
|---|---|---|
| Python | 3.13 (`>=3.13,<3.14`) | A plataforma é contrato, não sugestão |
| Sistema | Linux x86_64 | Outras plataformas não são suportadas na v1 |
| Node.js | 22+ | Só para construir o frontend e validar contratos |
| pnpm | 10.13.1 | Via `corepack enable` |
| Ollama | 0.32.5 | Servindo em `http://127.0.0.1:11434` |
| Brave | qualquer | `brave-browser`, `brave-browser-stable` ou `brave` no PATH, para escalação do `web_fetch` |

## Instalação

Duas formas, mesmo resultado. Escolha uma.

### Opção A: script

```bash
scripts/bootstrap.sh
```

Idempotente — pode rodar de novo sem duplicar trabalho. Faz, nesta ordem:

1. `uv sync` — instala as dependências Python e cria `.venv`.
2. `corepack enable` + `pnpm install && pnpm build` em `web/` — gera `web/dist`.
3. `ollama create mitos -f Modelfile` (só se o perfil `mitos` ainda não existir) e
   confere o digest do perfil instalado contra
   `installation.installed_profile_digest_sha256` em `config/model-profiles.json`,
   avisando no stderr se divergir.
4. Baixa o `tokenizer.json` do repositório do modelo base
   (`huihui-ai/Huihui-Qwen3.5-4B-abliterated`) para
   `$XDG_STATE_HOME/harness-2/tokenizer.json` (ou `~/.local/state/harness-2/` sem
   `XDG_STATE_HOME`), só se o arquivo ainda não existir ali.

Qualquer etapa que precise de uma ferramenta ausente no PATH (`corepack`, `ollama`)
é pulada com aviso — o script não falha por isso.

### Opção B: passo a passo manual

```bash
uv sync
```
Instala as dependências Python e cria `.venv`.

```bash
corepack enable && cd web && pnpm install && pnpm build && cd ..
```
Gera `web/dist`, servido pelo próprio backend.

```bash
ollama create mitos -f Modelfile
curl -s http://127.0.0.1:11434/api/tags \
  | python3 -c "import json,sys;print(next(m['digest'] for m in json.load(sys.stdin)['models'] if m['name']=='mitos:latest'))"
```
Cria o perfil de execução a partir do [`Modelfile`](Modelfile) da raiz. O segundo
comando imprime o digest do manifesto que o Ollama atribuiu ao modelo instalado,
e ele deve bater com `installation.installed_profile_digest_sha256` em
`config/model-profiles.json` — divergência é erro na inicialização, não aviso. É
esse mesmo campo que `OllamaRuntime.verify_profile` compara com `/api/tags`, por
isso o digest vem de lá e não de um hash do texto do Modelfile: são valores
diferentes, e só um deles identifica os pesos e os parâmetros de fato instalados.

```bash
mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/harness-2"
curl -fsSL \
  https://huggingface.co/huihui-ai/Huihui-Qwen3.5-4B-abliterated/resolve/main/tokenizer.json \
  -o "${XDG_STATE_HOME:-$HOME/.local/state}/harness-2/tokenizer.json"
```
Baixa o `tokenizer.json` para o mesmo caminho que o setup vai sugerir por padrão
(veja abaixo). Pode ir para qualquer outro caminho, desde que informe esse caminho
no setup.

### Tokenizer

O orçamento de contexto (32768 tokens) depende de uma contagem real de tokens, não
de estimativa por caractere. O harness carrega um `tokenizer.json` no formato
HuggingFace: sem o arquivo, a aplicação sobe com
`EngineReadiness(ready=False, reason_code="tokenizer_file_missing")` e recusa
execuções. O arquivo não é versionado neste repositório (12 MB, e o conteúdo
correto depende de qual revisão do modelo você instalou). Download direto:
[`tokenizer.json`](https://huggingface.co/huihui-ai/Huihui-Qwen3.5-4B-abliterated/resolve/main/tokenizer.json)
do repositório do modelo base.

O SHA-256 do tokenizer **não é digitado no setup**: o harness calcula o digest do
arquivo apontado e grava esse valor em `host.json#tokenizer_digest`, usado depois
em todo boot para detectar o arquivo trocado.

## Primeira execução

```bash
uv run harness       # ou: uv run uvicorn harness.api:create_app --factory --host 127.0.0.1 --port 8765
```

Na primeira execução o servidor imprime um **token de setup efêmero** no stderr
(TTL de 600 s). Abra `http://127.0.0.1:8765/setup`: o formulário já vem
pré-preenchido com o diretório de estado e o caminho do tokenizer sugeridos (o
mesmo que o `bootstrap.sh` usa) e com a origin atual — falta só colar o token,
apontar as raízes de workspace e confirmar. Sem interface, o mesmo é feito por
`POST /api/setup` com o header `X-Harness-Setup-Token`, informando
`allowed_workspace_roots`, `tokenizer_path`, `state_dir`, `allowed_origins` e,
opcionalmente, a chave da Brave Search. `GET /api/setup/status` diz se ainda é
necessário e devolve os valores sugeridos em `suggested_state_dir` e
`suggested_tokenizer_path`.

- `allowed_workspace_roots` — diretórios que as tools de arquivo do agente (`workspace_read`/`workspace_write`) podem tocar, um caminho absoluto por linha. É o sandbox: caminho fora dessas raízes é recusado mesmo que o modelo peça. Tipicamente o(s) diretório(s) de projeto que você vai trabalhar com o agente.
- `allowed_origins` — de onde o navegador pode chamar esta API: protocolo, host e porta (ex. `http://127.0.0.1:8765`), um por linha. Requisição HTTP com header `Origin` fora dessa lista é recusada. Normalmente é só a própria origin em que você está acessando o painel — a UI já pré-preenche com ela.

Depois de configurado, o setup só reabre com `HARNESS_SETUP_REOPEN=1`.

## Configuração por ambiente

Todas as variáveis são opcionais; os valores efetivos vêm do HostConfig gravado no
setup, e a variável de ambiente tem precedência sobre ele.

| Variável | Default | Efeito |
|---|---|---|
| `HARNESS_HOST` | `127.0.0.1` | Interface do servidor. Ver "Exposição de rede" |
| `HARNESS_PORT` | `8765` | Porta do servidor |
| `HARNESS_CONFIG` | `config/harness.json` | Contrato normativo carregado |
| `HARNESS_STATE_DIR` | `$XDG_STATE_HOME/harness-2` | SQLite canônico, telemetria e tokenizer |
| `HARNESS_WORKSPACE_ROOTS` | do HostConfig | Raízes permitidas, separadas por `:` |
| `HARNESS_TOKENIZER_PATH` | `<state_dir>/tokenizer.json` | Caminho do tokenizer |
| `HARNESS_TOKENIZER_SHA256` | do HostConfig | Digest exigido do tokenizer |
| `HARNESS_OLLAMA_URL` | `http://127.0.0.1:11434` | Endpoint do runtime |
| `HARNESS_BRAVE_API_KEY` | — | Chave da Brave Search. Mutuamente exclusiva com a de arquivo |
| `HARNESS_BRAVE_API_KEY_FILE` | — | Caminho absoluto de arquivo privado (modo `0600`, sem symlink) com a chave |
| `HARNESS_HOST_CONFIG` | padrão do XDG | Caminho do HostConfig |
| `HARNESS_ALLOWED_ORIGINS` | `127.0.0.1` e `localhost` na porta do servidor | Allowlist de Origin, separada por vírgula |
| `HARNESS_SETUP_REOPEN` | `0` | Reabre o setup numa instalação já configurada. `HARNESS_SETUP` é aceito como alias |

Um arquivo `.env` na raiz preenche essas variáveis — copie de `.env.example`. Ele
é lido do diretório de trabalho, como `config/harness.json`, e vale para os dois
jeitos de subir o servidor. Variável já exportada no ambiente vence o arquivo, e
nada de `config/*.json` entra por ali: contrato é selado por digest e não é
ajustável por ambiente.

Segredo continua sendo assunto do setup, que grava no CredentialStore com escrita
atômica e permissão privada. Se ainda assim a chave da Brave estiver no `.env`,
ele precisa ser `0600` — com bit de grupo ou de outros o harness recusa a partida.

### Exposição de rede

A exposição é derivada da credencial, não de uma flag:

| Estado | Comportamento |
|---|---|
| Sem senha de Operator | Só conexões de loopback direto são atendidas. Qualquer outra recebe `401 authentication_required`. |
| Com senha de Operator | Toda rota da API exige o header `X-Harness-Session` obtido em `POST /api/session`. |

Não existe configuração que abra a porta para a rede sem autenticação. Exceções
de rota, e por quê: `GET /api/health` (responde a supervisor antes de haver
login), `/api/setup*` (guardada pelo token efêmero e restrita a loopback) e
`POST /api/session` (é o login).

Definir a primeira senha:

```bash
curl -X PUT http://127.0.0.1:8765/api/admin/operator-password \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8765' \
  -d '{"password": "pelo-menos-doze-caracteres"}'
```

Rotacionar exige a senha atual em `current_password`, além da sessão:

```bash
curl -X PUT http://127.0.0.1:8765/api/admin/operator-password \
  -H 'Content-Type: application/json' \
  -H 'Origin: http://127.0.0.1:8765' \
  -H "X-Harness-Session: $SESSION" \
  -d '{"current_password": "a-senha-de-agora", "password": "a-nova-senha"}'
```

Trocar a senha invalida todas as sessões abertas. O hash é PBKDF2-HMAC-SHA256
com 600.000 iterações; a senha original nunca é gravada nem devolvida.

O modelo de ameaça está em [`docs/THREAT-MODEL-AUTH.md`](docs/THREAT-MODEL-AUTH.md).

### Reverse proxy

Para publicar além do loopback, configure a senha e coloque um proxy com TLS à
frente. O harness recusa qualquer requisição que traga `Forwarded`, `Via`,
`X-Real-IP` ou `X-Forwarded-*` como se fosse local, então o proxy nunca consegue
se passar por conexão direta — mas você precisa incluir a origem pública em
`HARNESS_ALLOWED_ORIGINS`:

```nginx
server {
  listen 443 ssl;
  server_name harness.example;

  location / {
    proxy_pass http://127.0.0.1:8765;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_http_version 1.1;
    proxy_buffering off;              # o stream AG-UI é SSE
    proxy_read_timeout 930s;          # acima de loop.max_turn_duration_seconds
  }
}
```

```bash
HARNESS_ALLOWED_ORIGINS=https://harness.example uv run harness
```

## System prompt

`SYSTEM-PROMPT.md`, na raiz, mostra o texto exato que o modelo recebe. O arquivo
tem duas metades separadas pela marca `<!-- OPERATOR -->`:

- **acima**, um espelho do prompt derivado dos contratos. É documentação: editar
  ali não muda nada. Para mudar esse texto, mude `src/harness/system_prompt.py`
  ou as capacidades em `config/model-profiles.json` e resele com
  `uv run python scripts/seal-system-prompt.py` — o `{{TODAY}}` do espelho é
  substituído pela data do host a cada Turn;
- **abaixo**, o seu texto, anexado ao fim do prompt. Bloco vazio ou arquivo
  ausente deixam o prompt exatamente como o de cima, byte a byte. O limite é
  4000 caracteres, porque ele entra em todo Turn e disputa o orçamento de
  contexto com o histórico.

Cada execução de eval congela o digest desse bloco junto dos digests de
contrato: dois braços com textos de Operator diferentes não são o mesmo sistema
e não devem ser comparados como se fossem.

## Uso

Abra `http://127.0.0.1:8765`. O produto é a superfície web — CLI e desktop estão
fora do roadmap da v1 (`docs/RELEASE-PENDING.md`).

Cada Conversation começa sem autoridade nenhuma. Ler arquivos exige um
`WorkspaceRootGrant`, escrever exige também `WriteGrant`, e qualquer saída de dados
exige `WebAccessGrant`. O modelo não concede nem amplia acesso: quem concede é o
Operator, pela interface.

As 8 tools expostas: `read_file`, `write_file`, `edit`, `list_directory`, `glob`,
`grep_search`, `web_search`, `web_fetch`.

## Desenvolvimento

```bash
node scripts/validate-contracts.mjs        # coerência entre docs e contratos JSON
node scripts/validate-contracts.mjs --write  # resela os digests derivados após editar contratos
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest
cd web && pnpm test && pnpm exec tsc -b
```

Os JSON de `config/` e `evals/` carregam digests derivados do próprio conteúdo.
Depois de editar qualquer contrato ou fixture, rode o validador com `--write` para
reselar e commite a mudança de digest junto.

## Avaliações

`evals/fixtures/regressions.json` guarda o corpus de regressões determinísticas e
`evals/experiments.json` a matriz de experimentos. O protocolo está em
[`evals/README.md`](evals/README.md): piloto de 15 execuções por braço (direcional),
promoção com 50 execuções por braço e 3 seeds registradas. Violação de segurança
reprova imediatamente, independente do resto.

Dos experimentos declarados, apenas `guarded_web_brave_escalation` é
`required_before_release`. Os demais travam **mudanças de baseline** (temperatura,
thinking, limite de passos, idioma do prompt, verificação de página) e não são
dívida de release: enquanto não rodarem, o baseline vigente permanece o que está
no contrato.

Medição inventada para preencher contrato é proibida (`docs/DECISOES-2.0.md`). Um
experimento sem execução fica com `result: null`.

## Licença

[Apache-2.0](LICENSE).

## Documentação

Ordem de leitura em [`docs/README.md`](docs/README.md). Em resumo: o glossário
canônico está em [`CONTEXT.md`](CONTEXT.md), o baseline normativo em
`docs/DECISOES-2.0.md`, as exclusões da release em `docs/RELEASE-PENDING.md` e as
fronteiras arquiteturais em `docs/adr/`.
