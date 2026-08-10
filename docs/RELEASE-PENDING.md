# Pendências da primeira release

> Estado: contrato de exclusão da primeira release  
> Data: 10 de agosto de 2026

Este documento registra o que deliberadamente não entra na primeira release. Ausência aqui não autoriza implementação: qualquer entrada exige atualização conjunta de [`DECISOES-2.0.md`](DECISOES-2.0.md), contratos, fixtures e evidência de gate.

## Capacidades adiadas

| Item | Motivo da exclusão | Gate e evidência para entrada |
|---|---|---|
| vLLM | Não existe instalação local fixada nem round-trip validado para reasoning e tools; o primeiro release tem Ollama como único runtime funcional. | RuntimeProfile completo, versão/digest do runtime e componentes, suíte de parser sem corrupção de whitespace, zero tool calls ocultas e evals web fim a fim. |
| Outros runtimes locais | SGLang, Transformers e llama.cpp/GGUF aparecem na pesquisa, mas não têm instalação, adaptador e manifesto aprovados neste Workspace. | RuntimeProfile reproduzível por candidato, paridade de ResultPayload, parser/template fixos e os mesmos gates web, de segurança e de desempenho do Ollama. |
| Backend remoto | Todo ModelView pode deixar a máquina a cada AgentStep e ainda não há fluxo de consentimento nem contrato de provedor. | Consentimento explícito e revogável, ausência de fallback silencioso, manifesto reproduzível, redaction testada, auditoria de `data_egress` e evals de privacidade. |
| Visão | A declaração do runtime não prova qualidade do artefato exato; a herança registrou leitura de `24.7` como `247`. | Evidência no digest local exato, orçamento multimodal, limites de arquivos, aviso de incerteza e corpus com texto pequeno, números e falhas adversariais. |
| Shell | WorkspaceRootGrant não contém execução arbitrária, subprocessos podem escapar da política de arquivos e timeouts com efeitos exigem reconciliação. | Sandbox separado, allowlist, WriteGrant específico, aprovação explícita, limites, auditoria, idempotência e zero violações na suíte de segurança. |
| Python arbitrário/code interpreter | Equivale a execução arbitrária e não é contido pela policy de paths ou pelo runtime Python da aplicação. | Mesmo gate de sandbox do shell, ambiente descartável sem segredos, limites de recursos e corpus específico de exfiltração e persistência. |
| Streaming | Fragmentos podem conter XML incompleto, tool calls ou reasoning e não podem disparar efeitos com segurança. | Teste diferencial contra geração completa, parser incremental sem execução antecipada, cancelamento consistente e nenhuma divergência de ToolResult. |
| Paralelismo | Ordem e independência de efeitos ainda não são provadas; quatro chamadas por AgentStep não autorizam execução simultânea. | Classificador mecânico de independência, somente operações idempotentes sem efeitos conflitantes, ordenação determinística de resultados e evals de corrida/replay. |
| Compressão de código | O ganho histórico de 28% não foi revalidado contra fidelidade no ModelView do 2.0. | Experimento com round-trip, preservação byte a byte quando necessária, medição de tokens e ausência de regressão de tarefa; permanece `disabled_experimental`. |
| Verificação automática de página | A automação e a identidade PageRevision estão contratadas, mas o ganho e a latência ainda não foram medidos no 2.0. | Experimento `page_verification_once_per_page_revision` com validade do HTML, zero repetição da mesma chave, abandono e latência; até lá o modo é `disabled`. |
| Branching de conversa | Ramificações tornam ambíguos CanonicalHistory, grants, revisão de workspace e retenção. | Semântica de ancestralidade e merge definida, isolamento de grants/workspace, projeção AG-UI e evals de concorrência e auditoria. |
| Approve-with-edits | Alterar argumentos durante aprovação muda autoria, idempotência e o ToolResult esperado. | Contrato de autoria e diff, nova validação após edição, nova idempotency key, trilha de auditoria e testes de TOCTOU. |
| Dark mode | A linguagem visual aprovada para a primeira release é clara e fixa; tema não deve atrasar fluxos e acessibilidade centrais. | Tokens de tema estabilizados, contraste WCAG, componentes nativos e de tools verificados e regressão visual web em navegadores suportados. |
| Contexto longo e YaRN | O limite local medido é 24.576; 32k ou mais compete com VRAM e não tem ganho de tarefa demonstrado. | RuntimeProfile/ExecutionRoute separados, medição de memória e qualidade no comprimento-alvo e nenhuma regressão nas tarefas curtas. |
| RAG, embeddings e memória operacional | Não há demanda medida e a proveniência de fatos materializados ainda não tem contrato. | Casos reais que falhem sem recuperação, ownership/proveniência definidos, eval comparativo e política de retenção/privacidade. |
| Seleção dinâmica, `tool_search` e tools adicionais | O registry atual é pequeno e fixo; roteamento adicionaria outra decisão falível. `delete_file`, delegação, monitor e interação especializada não têm fluxo aprovado. | Degradação medida por tamanho ou requisito de produto; cada nova tool exige efeito, grants, schema, ResultPayload, UX web e evals próprios. |
| Telemetria externa | Traces podem exportar prompts, arquivos, páginas e respostas; o store local sem conteúdo é suficiente para o baseline. | WebAccessGrant explícito para observabilidade, redaction verificável, inventário de campos e teste que prove ausência de conteúdo/segredos. |
| Retenção parcial ou por Conversation | Buracos silenciosos quebram CanonicalHistory e overrides individuais tornam a policy imprevisível. | Não entra nesse formato. Só uma nova decisão arquitetural, com prova de integridade, pode substituir retenção global por Conversation inteira. |
| CLI, desktop e outras superfícies | O produto e seus evals são web-first; outra superfície duplicaria contrato de interação e acessibilidade. | Necessidade medida, protocolo de projeção definido e suíte de tarefa completa equivalente à web. |

## Fora do roadmap

| Item | Decisão | Condição excepcional para reconsiderar |
|---|---|---|
| Qwen2.5-Coder-7B | Removido do roadmap. O comparativo existente permanece apenas como evidência histórica e não define challenger, experimento ou destino de release. | Nova decisão explícita baseada em necessidade não atendida pelo RuntimeProfile vigente; se reaberto, exige manifesto completo e os mesmos gates de segurança e tarefa, sem tratamento preferencial. |

## Limitações residuais aceitas

| Limitação | Impacto conhecido | Evidência ou gate de remoção |
|---|---|---|
| Ollama local é o único runtime funcional | Não há portabilidade operacional na primeira release. | Um segundo RuntimeProfile só entra após cumprir seu gate acima sem alterar silenciosamente a ExecutionRoute ativa. |
| Verificação de página começa desligada | HTML entregue não recebe validação automática no baseline. | Promover `automatic_once_per_page_revision` após o experimento registrar validade, repetição, abandono e latência. |
| DNS rebinding entre validação e conexão | A revalidação de DNS/redirect reduz, mas não elimina, TOCTOU de rede. | Transporte que conecte ao endereço validado, testes de rebinding e revisão do threat model. |
| Telemetria não armazena conteúdo | Diagnóstico depende de IDs, hashes, classes, tamanhos e reprodução isolada. | Só muda com decisão de privacidade explícita; não é gate da primeira release. |
| Raciocínio não é persistido | Replay e auditoria não mostram o reasoning transitório visto ao vivo. | Limitação deliberada; qualquer mudança exige revisão de privacidade e separação inequívoca de CanonicalHistory. |
| Retenção usa uma política global | Não há TTL por conversa, workspace ou Operator, nem remoção parcial do histórico. | Demanda medida, política global configurável e teste de exclusão da Conversation inteira sem lacunas. |
| Plataforma suportada é Python 3.13 em Linux x86_64 | Outros sistemas, arquiteturas e versões podem falhar sem diagnóstico específico. | Matriz CI/evals web na nova plataforma e RuntimeProfile com evidência reproduzível. |
| UX e avaliações são web-first | CLI, desktop e comportamento sem navegador não são superfícies de release. | Contrato próprio de superfície, acessibilidade e suíte equivalente de tarefa completa. |
