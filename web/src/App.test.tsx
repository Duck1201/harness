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

  it("aprova a escrita com dado da web só depois da decisão do Operator", async () => {
    const user = userEvent.setup();
    const client = new MockHarnessClient();
    client.seedConfirmation({
      id: "turn-1-confirmation-2",
      conversation_id: "chat-128",
      turn_id: "turn-1",
      step_sequence: 2,
      reason_code: "web_taint_confirmation_required",
      tool_calls: [
        {
          id: "write-1",
          name: "write_file",
          arguments: { file_path: "relatorio.md", content: "vindo da web" },
        },
      ],
    });
    const resolve = vi.spyOn(client, "resolveConfirmation");
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "Escrita com dado da web" }),
    ).toBeInTheDocument();
    expect(screen.getByText("write_file")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Aprovar esta escrita" }));

    await waitFor(() =>
      expect(resolve).toHaveBeenCalledWith("chat-128", "turn-1-confirmation-2", true),
    );
    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "Escrita com dado da web" })).toBeNull(),
    );
  });

  it("exige o setup antes de qualquer outra área e pede reinício ao concluir", async () => {
    const user = userEvent.setup();
    const client = new MockHarnessClient();
    client.seedSetupRequired();
    const complete = vi.spyOn(client, "completeSetup");
    const chat = vi.spyOn(client, "getChatSnapshot");
    render(<App client={client} />);

    expect(
      await screen.findByRole("heading", { name: "Configurar este host" }),
    ).toBeInTheDocument();
    expect(chat).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("Token de setup"), "token-do-stderr");
    await user.type(
      screen.getByLabelText("Raízes de Workspace autorizadas"),
      "/workspaces/harness-2",
    );
    await user.type(screen.getByLabelText("Diretório de estado"), "/var/lib/harness-2");
    await user.type(
      screen.getByLabelText("Caminho do tokenizer.json"),
      "/var/lib/harness-2/tokenizer.json",
    );
    await user.type(screen.getByLabelText("SHA-256 do tokenizer"), "a".repeat(64));
    await user.type(screen.getByLabelText("Origins autorizadas"), "http://127.0.0.1:8765");
    await user.click(screen.getByRole("button", { name: "Concluir setup" }));

    await waitFor(() =>
      expect(complete).toHaveBeenCalledWith("token-do-stderr", {
        allowed_workspace_roots: ["/workspaces/harness-2"],
        state_dir: "/var/lib/harness-2",
        tokenizer_path: "/var/lib/harness-2/tokenizer.json",
        tokenizer_digest: "a".repeat(64),
        allowed_origins: ["http://127.0.0.1:8765"],
        brave_api_key: null,
      }),
    );
    expect(
      await screen.findByRole("heading", { name: "Configuração gravada" }),
    ).toBeInTheDocument();
  });

  it("pede a senha antes de qualquer área quando o host exige autenticação", async () => {
    const user = userEvent.setup();
    const client = new MockHarnessClient();
    client.seedAuthenticationRequired("operator-password-1");
    const chat = vi.spyOn(client, "getChatSnapshot");
    const login = vi.spyOn(client, "login");
    render(<App client={client} />);

    expect(await screen.findByRole("heading", { name: "Entrar no Harness" })).toBeInTheDocument();
    expect(chat).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("Senha de Operator"), "wrong-password");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Senha de Operator"), "operator-password-1");
    await user.click(screen.getByRole("button", { name: "Entrar" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("operator-password-1"));
    expect(
      await screen.findByRole("heading", { name: "Refinar retenção por conversa" }),
    ).toBeInTheDocument();
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
