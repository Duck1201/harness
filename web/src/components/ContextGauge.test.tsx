import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ContextUsage } from "../types";
import { ContextGauge } from "./ContextGauge";

const usage: ContextUsage = {
  inputTokens: 13107,
  contextWindow: 65536,
  outputBudget: 8192,
  droppedTurns: 0,
};

describe("ContextGauge", () => {
  it("mostra o consumo contra a janela inteira e expõe a barra a leitor de tela", () => {
    render(<ContextGauge usage={usage} />);

    expect(screen.getByText("13.107")).toBeTruthy();
    expect(screen.getByText(/de 65\.536 tokens/)).toBeTruthy();
    expect(screen.getByText("20.0%")).toBeTruthy();
    expect(screen.getByText(/8\.192 reservados/)).toBeTruthy();

    const bar = screen.getByRole("progressbar");
    expect(bar.getAttribute("aria-valuenow")).toBe("13107");
    expect(bar.getAttribute("aria-valuemax")).toBe("65536");
  });

  it("só fala em turno descartado quando algum caiu, e concorda em número", () => {
    const { rerender } = render(<ContextGauge usage={usage} />);
    expect(screen.queryByText(/descartad/)).toBeNull();

    rerender(<ContextGauge usage={{ ...usage, droppedTurns: 1 }} />);
    expect(screen.getByText("1 turno descartado")).toBeTruthy();

    rerender(<ContextGauge usage={{ ...usage, droppedTurns: 3 }} />);
    expect(screen.getByText("3 turnos descartados")).toBeTruthy();
  });

  it("muda de faixa quando a janela aperta, que é quando o histórico começa a cair", () => {
    const { container, rerender } = render(<ContextGauge usage={usage} />);
    expect(container.querySelector(".context-gauge-calm")).toBeTruthy();

    rerender(<ContextGauge usage={{ ...usage, inputTokens: 49152 }} />);
    expect(container.querySelector(".context-gauge-warning")).toBeTruthy();

    rerender(<ContextGauge usage={{ ...usage, inputTokens: 62259 }} />);
    expect(container.querySelector(".context-gauge-critical")).toBeTruthy();
  });
});
