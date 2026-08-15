import { Gauge } from "lucide-react";

import type { ContextUsage } from "../types";

/**
 * Quanto da janela de contexto o passo atual consumiu.
 *
 * A conta é sobre o espaço que a entrada pode ocupar, não sobre a janela
 * inteira: `ContextBuilder` só aceita um contexto quando entrada mais reserva
 * de saída cabem na janela, então a entrada nunca passa de janela menos
 * reserva. Medido contra a janela cheia, 100% seria inalcançável e a faixa
 * crítica nunca acenderia — foi o que aconteceu num turno de 64k, onde o teto
 * real era 87,5%. Contra o utilizável, 100% quer dizer o que o Operator
 * precisa saber: daqui em diante o histórico começa a cair, e os turnos
 * descartados ao lado são esse sintoma já acontecendo.
 */
export function ContextGauge({ usage }: { usage: ContextUsage }) {
  const { inputTokens, contextWindow, outputBudget, droppedTurns } = usage;
  const usable = Math.max(contextWindow - outputBudget, 1);
  const percent = (inputTokens / usable) * 100;
  const level = percent >= 90 ? "critical" : percent >= 70 ? "warning" : "calm";

  return (
    <div className={`context-gauge context-gauge-${level}`}>
      <div className="context-gauge-head">
        <Gauge size={14} />
        <span>
          <strong>{format(inputTokens)}</strong> de {format(usable)} tokens utilizáveis
        </span>
        <span className="context-gauge-percent">{percent.toFixed(1)}%</span>
      </div>
      <div
        className="context-gauge-track"
        role="progressbar"
        aria-label="Uso da janela de contexto"
        aria-valuenow={inputTokens}
        aria-valuemin={0}
        aria-valuemax={usable}
      >
        <div className="context-gauge-fill" style={{ width: `${Math.min(percent, 100)}%` }} />
      </div>
      <div className="context-gauge-foot">
        <span>
          janela de {format(contextWindow)}, {format(outputBudget)} reservados para a resposta
        </span>
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
