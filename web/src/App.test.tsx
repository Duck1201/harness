import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MockHarnessClient } from "./client/MockHarnessClient";
import { App } from "./App";

describe("App", () => {
  it("navega entre as três áreas públicas", async () => {
    const user = userEvent.setup();
    render(<App client={new MockHarnessClient()} />);

    expect(await screen.findByRole("heading", { name: "Refinar retenção por conversa" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Evals" }));
    expect(screen.getByRole("heading", { name: "Evals" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Settings" }));
    expect(screen.getByRole("heading", { name: "Settings" })).toBeInTheDocument();
    expect(screen.getByDisplayValue("local_mitos_ollama_reproduction")).toBeInTheDocument();
  });

  it("chama o client para seleção, criação, fila, grants e feedback", async () => {
    const user = userEvent.setup();
    const client = new MockHarnessClient();
    const select = vi.spyOn(client, "selectConversation");
    const create = vi.spyOn(client, "createConversation");
    const edit = vi.spyOn(client, "editPendingRequest");
    const cancel = vi.spyOn(client, "cancelPendingRequest");
    const revoke = vi.spyOn(client, "revokeGrant");
    const addGrant = vi.spyOn(client, "addGrant");
    const feedback = vi.spyOn(client, "addFeedback");
    render(<App client={client} />);

    expect(await screen.findByRole("heading", { name: "Refinar retenção por conversa" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Editar solicitação 1" }));
    const editor = screen.getByRole("textbox", { name: "Editar solicitação 1" });
    await user.clear(editor);
    await user.type(editor, "Fila editada pela API");
    await user.click(screen.getByRole("button", { name: "Salvar solicitação 1" }));
    await waitFor(() => expect(edit).toHaveBeenCalledWith("chat-128", "pending-1", "Fila editada pela API"));

    await user.click(screen.getByRole("button", { name: "Revogar Write" }));
    await waitFor(() => expect(revoke).toHaveBeenCalledWith("chat-128", "grant-write"));
    await user.click(await screen.findByRole("button", { name: "Ativar Write" }));
    await waitFor(() => expect(addGrant).toHaveBeenCalledWith("chat-128", "WriteGrant"));

    await user.click(screen.getByRole("button", { name: /Funcionou/ }));
    await waitFor(() => expect(feedback).toHaveBeenCalledWith("chat-128", 1, "turn-1"));

    await user.click(screen.getByRole("button", { name: "Cancelar solicitação 1" }));
    await waitFor(() => expect(cancel).toHaveBeenCalledWith("chat-128", "pending-1"));

    await user.click(screen.getByRole("button", { name: /Verificação de página/ }));
    await waitFor(() => expect(select).toHaveBeenCalledWith("chat-127"));

    await user.click(screen.getByRole("button", { name: "Nova Conversation" }));
    const name = screen.getByRole("textbox", { name: "Nome" });
    await user.clear(name);
    await user.type(name, "Conversation criada");
    await user.click(screen.getByRole("button", { name: "Criar" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith("/workspaces/harness-2", "Conversation criada"),
    );
  });

  it("envia RunAgentInput, chama Stop e aborta o stream no unmount", async () => {
    const user = userEvent.setup();
    const client = new MockHarnessClient();
    let streamSignal: AbortSignal | undefined;
    const stream = vi.spyOn(client, "streamAgent").mockImplementation(
      async (input, onEvent, signal) => {
        streamSignal = signal;
        onEvent({ type: "RUN_STARTED", threadId: input.threadId, runId: input.runId! });
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        });
        return { type: "RUN_FINISHED", threadId: input.threadId, runId: input.runId! };
      },
    );
    const stop = vi.spyOn(client, "stop");
    const view = render(<App client={client} />);

    await screen.findByRole("heading", { name: "Refinar retenção por conversa" });
    await user.type(screen.getByLabelText("Solicitação para o Harness"), "Nova solicitação");
    await user.click(screen.getByRole("button", { name: "Enviar solicitação" }));

    await waitFor(() => expect(stream).toHaveBeenCalled());
    expect(stream.mock.calls[0]?.[0]).toMatchObject({
      threadId: "chat-128",
      messages: [{ role: "user", content: "Nova solicitação" }],
    });
    await user.click(screen.getByRole("button", { name: /Stop/ }));
    await waitFor(() => expect(stop).toHaveBeenCalledWith("chat-128"));

    view.unmount();
    expect(streamSignal?.aborted).toBe(true);
  });
});
