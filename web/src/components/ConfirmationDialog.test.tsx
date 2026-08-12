import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmationDialog } from "./ConfirmationDialog";
import type { PendingConfirmation } from "../types";

function confirmation(
  reason_code: string,
  overrides: Partial<PendingConfirmation> = {},
): PendingConfirmation {
  return {
    id: "turn-1-confirmation-1",
    conversation_id: "chat-1",
    turn_id: "turn-1",
    step_sequence: 1,
    reason_code,
    tool_calls: [
      { id: "write-1", name: "write_file", arguments: { file_path: "nota.md" } },
    ],
    previews: [
      {
        tool_call_id: "write-1",
        path: "nota.md",
        kind: "create",
        diff: "@@ -0,0 +1 @@\n+nota",
        truncated: true,
      },
    ],
    ...overrides,
  };
}

describe("ConfirmationDialog", () => {
  it("pede o grant que falta em vez de mandar o Operator procurar o chip", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    render(
      <ConfirmationDialog
        confirmation={confirmation("write_grant_required")}
        busy={false}
        onResolve={onResolve}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Permitir escrita nesta conversa" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/concede o WriteGrant/)).toBeInTheDocument();
    expect(screen.getByText(/Diff cortado/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Permitir escrita" }));
    expect(onResolve).toHaveBeenCalledWith(true, false);
  });

  it("pede o grant de web sem prometer dispensa que a policy não guarda", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    render(
      <ConfirmationDialog
        confirmation={confirmation("web_access_grant_required")}
        busy={false}
        onResolve={onResolve}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Permitir acesso à web nesta conversa" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/sem reenviar o prompt/)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Permitir acesso" }));
    expect(onResolve).toHaveBeenCalledWith(true, false);
  });

  it("sob taint, nomeia a decisão pelo efeito da chamada e não sempre por escrita", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    render(
      <ConfirmationDialog
        confirmation={confirmation("web_taint_confirmation_required", {
          tool_calls: [
            {
              id: "search-1",
              name: "web_search",
              arguments: { query: "searxng" },
              effects: ["data_egress"],
            },
          ],
          previews: [],
        })}
        busy={false}
        onResolve={onResolve}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "Novo acesso à web com dado da web" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/sair para a rede de novo/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Aprovar acesso" }));
    expect(onResolve).toHaveBeenCalledWith(true, false);
  });

  it("nega sem dispensar e mantém o foco inicial no botão seguro", async () => {
    const user = userEvent.setup();
    const onResolve = vi.fn();
    render(
      <ConfirmationDialog
        confirmation={confirmation("write_confirmation_required")}
        busy={false}
        onResolve={onResolve}
      />,
    );

    expect(screen.getByRole("button", { name: "Negar" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Negar" }));
    expect(onResolve).toHaveBeenCalledWith(false, false);
  });
});
