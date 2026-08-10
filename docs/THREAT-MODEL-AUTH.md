# Modelo de ameaça: autenticação do Operator

> Estado: vigente para a primeira release
> Data: 10 de agosto de 2026
> Decisão: [`adr/0005-operator-authentication-and-network-exposure.md`](adr/0005-operator-authentication-and-network-exposure.md)

O harness executa efeitos reais no Workspace e na rede sob a autoridade de quem
usa a interface. Antes desta decisão a única defesa era a allowlist de `Origin` e
o token efêmero de setup: qualquer processo capaz de abrir uma conexão TCP para a
porta assumia a identidade do Operator.

## Ativos

O conteúdo dos Workspaces autorizados, os grants ativos de cada Conversation, o
CanonicalHistory com o histórico integral e as credenciais no CredentialStore.

## Ameaças e mitigações

| Ameaça | Mitigação |
|---|---|
| Outro host da rede alcança a porta | Sem credencial configurada, o servidor recusa toda requisição que não venha de loopback direto. Com credencial, exige sessão autenticada. Não há terceira opção. |
| Página maliciosa dispara requisição do navegador do Operator | Allowlist de `Origin` mais sessão em header próprio: um formulário cross-site não consegue definir `X-Harness-Session`, e cookies não são usados. |
| Token de sessão vazado em log, histórico ou referrer | O token é opaco, aleatório de 256 bits, vive só em memória, expira em 12 h e nunca aparece em URL. |
| Força bruta da senha | PBKDF2-HMAC-SHA256 com 600.000 iterações e sal por credencial; comparação em tempo constante. Mínimo de 12 caracteres. |
| Escalada do token de setup para sessão | São mecanismos distintos: o token de setup só autoriza `POST /api/setup`, exige loopback direto e morre no TTL. Ele nunca cria sessão. |
| Reverse proxy apresentando tráfego remoto como local | `_is_direct_loopback` recusa qualquer requisição que traga `Forwarded`, `Via`, `X-Real-IP` ou `X-Forwarded-*`, então um proxy não consegue se passar por conexão local. |
| Senha lida do disco por outro processo do host | Só o hash é gravado, em arquivo `0600` com escrita atômica; a senha original nunca é persistida nem devolvida por rota alguma. |
| Sessão sobrevive à troca de senha | Rotacionar ou remover a credencial invalida todas as sessões abertas. |
| Sessão roubada vira acesso permanente pela troca de senha | `PUT /api/admin/operator-password` exige `current_password` sempre que já existe credencial gravada, verificada em tempo constante antes da escrita. Uma sessão sozinha não rotaciona a credencial nem tranca o Operator para fora. |

## Fora do modelo

Multiusuário, papéis distintos, auditoria por identidade e recuperação de senha.
A primeira release tem um único Operator. Rate limiting de login também fica de
fora: com loopback obrigatório na ausência de senha, e sem exposição pública
suportada, o custo do PBKDF2 é a defesa contra tentativa repetida.

## Rotas abertas sem sessão, e por quê

| Rota | Motivo |
|---|---|
| `GET /api/health` | Precisa responder a um supervisor antes de qualquer login existir. Não devolve conteúdo, só prontidão e capacidades. |
| `GET /api/setup/status`, `POST /api/setup` | Protegidas pelo próprio token efêmero de setup e restritas a loopback direto. |
| `POST /api/session` | É o login. |

## Exposição em rede

Publicar o harness numa LAN exige senha configurada. Publicá-lo na internet exige
TLS terminado por um reverse proxy à frente, com o `Origin` da URL pública em
`HARNESS_ALLOWED_ORIGINS`. O guia operacional está no [`README`](../README.md).
