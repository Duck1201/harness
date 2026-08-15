import { Gauge } from "lucide-react";

import type { ContextUsage } from "../types";

/**
 * Quanto da janela de contexto o passo atual consumiu.
 *
 * O número que importa é a entrada contra a janela inteira, não contra o espaço
 * livre: o orçamento de saída já está reservado dentro da mesma janela, e quem
 * lê a barra quer saber quando o histórico vai começar a cair. Por isso os
 * turnos descartados aparecem ao lado — eles são o sintoma, e chegam antes de o
 * Operator perceber que o modelo esqueceu alguma coisa.
 */
export function ContextGauge({ usage }: { usage: ContextUsage }) {
  const { inputTokens, contextWindow, outputBudget, droppedTurns } = usage;
  const percent = contextWindow > 0 ? (inputTokens / contextWindow) * 100 : 0;
  const level = percent >= 90 ? "critical" : percent >= 70 ? "warning" : "calm";

  return (
    <div className={`context-gauge context-gauge-${level}`}>
      <div className="context-gauge-head">
        <Gauge size={14} />
        <span>
          <strong>{format(inputTokens)}</strong> de {format(contextWindow)} tokens
        </span>
        <span className="context-gauge-percent">{percent.toFixed(1)}%</span>
      </div>
      <div
        className="context-gauge-track"
        role="progressbar"
        aria-label="Uso da janela de contexto"
        aria-valuenow={inputTokens}
        aria-valuemin={0}
        aria-valuemax={contextWindow}
      >
        <div className="context-gauge-fill" style={{ width: `${Math.min(percent, 100)}%` }} />
      </div>
      <div className="context-gauge-foot">
        <span>{format(outputBudget)} reservados para a resposta</span>
        {droppedTurns > 0 && (
          <span className="context-gauge-dropped">
            {droppedTurns} {droppedTurns === 1 ? "turno descartado" : "turnos descartados"}
          </span>
        )}
      </div>
    </div>
  );
}

function format(tokens: number): string {
  return tokens.toLocaleString("pt-BR");
}
