import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { TurnOutcome } from "./App";
import type { Grant } from "./types";

const writeGrant: Grant = {
  id: "grant-write",
  conversation_id: "chat-1",
  permission: "WriteGrant",
  scope: "workspace",
  granted_at: "2026-08-11T10:00:00Z",
  expires_at: null,
};

describe("TurnOutcome", () => {
  it("explica o bloqueio e oferece o grant faltante", async () => {
    const user = userEvent.setup();
    const onGrant = vi.fn();
    render(
      <TurnOutcome kind="blocked" reasonCode="write_grant_required" grants={[]} onGrant={onGrant} />,
    );

    expect(screen.getByText(/Ative o grant Write/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Ativar grant Write" }));
    expect(onGrant).toHaveBeenCalledWith("WriteGrant");
  });

  it("separa falha do provedor de falha do harness e mostra o detalhe", () => {
    const { rerender } = render(
      <TurnOutcome
        kind="failed"
        reasonCode="model_provider_unavailable"
        detail="ollama_transport_error: connection refused"
        grants={[]}
        onGrant={vi.fn()}
      />,
    );
    expect(screen.getByText(/Ollama não respondeu/)).toBeInTheDocument();
    expect(
      screen.getByText("ollama_transport_error: connection refused"),
    ).toBeInTheDocument();

    rerender(
      <TurnOutcome
        kind="failed"
        reasonCode="engine_error"
        detail="ValueError: harness bug"
        grants={[]}
        onGrant={vi.fn()}
      />,
    );
    expect(screen.getByText(/Falha interna do harness/)).toBeInTheDocument();
  });

  it("não oferece grant já ativo nem dica para código sem mapeamento", () => {
    const { rerender } = render(
      <TurnOutcome
        kind="blocked"
        reasonCode="write_grant_required"
        grants={[writeGrant]}
        onGrant={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button")).toBeNull();

    rerender(
      <TurnOutcome kind="completed" reasonCode="final_response" grants={[]} onGrant={vi.fn()} />,
    );
    expect(screen.getByText("final_response")).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
