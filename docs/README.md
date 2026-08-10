# Documentação do Harness 2.0

Contrato vigente e evidência histórica para implementar o Harness 2.0. A
primeira release tem um único RuntimeProfile funcional, local via Ollama; demais
runtimes e modelos citados na pesquisa não pertencem automaticamente ao roadmap.

## Ordem de leitura

1. [Glossário canônico](../CONTEXT.md) — linguagem de domínio usada pelos
   contratos.
2. [Decisões normativas do 2.0](DECISOES-2.0.md) — baseline vigente,
   invariantes e critérios formais para mudar uma decisão.
3. [Pendências da primeira release](RELEASE-PENDING.md) — exclusões, motivos,
   gates e limitações residuais.
4. [ADRs](adr/) — fronteiras arquiteturais difíceis de reverter.
5. [Pesquisa técnica completa](research/QWEN35_4B_PESQUISA.md) — fatos,
   inferências, limitações, backends, memória, segurança, lacunas e fontes.
6. [Cruzamento com a herança do 1.0](research/CRUZAMENTO_HERANCA_QWEN35_4B.md)
   — confronto entre documentação oficial e cerca de 240 turnos locais,
   convergências, contradições, limites de generalização e decisões.
7. [Comparativo histórico Qwen3.5-4B versus Qwen2.5-Coder-7B](research/COMPARATIVO_QWEN35_4B_VS_QWEN25_CODER_7B.md)
   — evidência preservada; Qwen2.5 foi removido do roadmap.
8. [Guia histórico de arquitetura](HARNESS_QWEN35_4B.md) e
   [guia histórico de tools](TOOLS_PARA_QWEN35.md) — pesquisa que explica a
   origem das decisões, sem competir com os contratos vigentes.

## Contratos executáveis

- [Modelfile do perfil local](../Modelfile)
- [RuntimeProfiles e evidência](../config/model-profiles.json)
- [Configuração normativa do harness](../config/harness.json)
- [Registry canônico de tools, automações e proibições](../config/tool-registry.json)
- [Corpus de regressões](../evals/fixtures/regressions.json)
- [Matriz de experimentos](../evals/experiments.json)
- [Como executar as avaliações](../evals/README.md)

Validação local, sem dependências externas:

```bash
node scripts/validate-contracts.mjs
```

O perfil instalado já foi recriado a partir do `Modelfile`; seu digest e a
evidência de coerência estão em `model-profiles.json`. Recriação futura é efeito
operacional deliberado e não faz parte da validação documental.

## Decisões que não devem se perder

- Separar RuntimeProfile de ExecutionRoute; identidade de runtime não é policy
  de execução.
- Fixar pesos, tokenizer/template, backend e parsers como uma única versão do
  sistema.
- Respeitar o formato XML-like oficial, não presumir o
  Hermes/JSON do Qwen3 anterior.
- No perfil local, começar com `temperature=0.3`, `presence_penalty=0`, contexto
  24.576 e o thinking já medido; toda mudança é um braço de bancada.
- Tratar seleção, argumentos e resultados do modelo como não confiáveis;
  validação, autorização, confirmação e sandbox pertencem ao harness.
- Tratar ModelView e AG-UI como projeções; CanonicalHistory permanece a fonte
  autoritativa e reasoning transitório não é persistido.

## Estado da evidência

A maior parte dos fatos vem do model card, dos arquivos do checkpoint e do
código/documentação oficial de Qwen, vLLM, Transformers, SGLang e llama.cpp.
Issues de upstream são usados como casos de regressão, não como prova de que
todas as versões falham. Guias e comparativos preservam a data e a conclusão da
pesquisa; somente `DECISOES-2.0.md` e os contratos JSON definem a release atual.
