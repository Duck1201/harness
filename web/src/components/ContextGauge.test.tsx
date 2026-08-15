import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ContextUsage } from "../types";
import { ContextGauge } from "./ContextGauge";

// Perfil real: janela de 64k com 8192 reservados para a resposta, ou seja 57344
// utilizáveis. É contra esse número que as faixas fazem sentido.
const usage: ContextUsage = {
  inputTokens: 11469,
  contextWindow: 65536,
  outputBudget: 8192,
  droppedTurns: 0,
};

describe("ContextGauge", () => {
  it("mede contra o utilizável, não contra a janela cheia", () => {
    render(<ContextGauge usage={usage} />);

    expect(screen.getByText("11.469")).toBeTruthy();
    expect(screen.getByText(/de 57\.344 tokens utilizáveis/)).toBeTruthy();
    expect(screen.getByText("20.0%")).toBeTruthy();
    expect(screen.getByText(/janela de 65\.536, 8\.192 reservados/)).toBeTruthy();

    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("11469");
    expect(bar.getAttribute("aria-valuemax")).toBe("57344");
  });

  it("chega ao crítico dentro do que o ContextBuilder deixa acontecer", () => {
    // 54205 foi o pico real observado num turno de seis leituras: 82,7% da
    // janela, mas 94,5% do utilizável. Medido contra a janela cheia esta faixa
    // seria inalcançável, porque a entrada nunca passa de janela menos reserva.
    const { container } = render(<ContextGauge usage={{ ...usage, inputTokens: 54205 }} />);

    expect(container.querySelector(".context-gauge-critical")).toBeTruthy();
    expect(screen.getByText("94.5%")).toBeTruthy();
  });

  it("muda de faixa quando a janela aperta, que é quando o histórico começa a cair", () => {
    const { container, rerender } = render(<ContextGauge usage={usage} />);
    expect(container.querySelector(".context-gauge-calm")).toBeTruthy();

    rerender(<ContextGauge usage={{ ...usage, inputTokens: 43008 }} />);
    expect(container.querySelector(".context-gauge-warning")).toBeTruthy();

    rerender(<ContextGauge usage={{ ...usage, inputTokens: 51610 }} />);
    expect(container.querySelector(".context-gauge-critical")).toBeTruthy();
  });

  it("só fala em turno descartado quando algum caiu, e concorda em número", () => {
    const { rerender } = render(<ContextGauge usage={usage} />);
    expect(screen.queryByText(/descartad/)).toBeNull();

    rerender(<ContextGauge usage={{ ...usage, droppedTurns: 1 }} />);
    expect(screen.getByText("1 turno descartado")).toBeTruthy();

    rerender(<ContextGauge usage={{ ...usage, droppedTurns: 3 }} />);
    expect(screen.getByText("3 turnos descartados")).toBeTruthy();
  });
});
