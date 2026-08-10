import {
  Activity,
  Archive,
  ArrowUp,
  AtSign,
  Bot,
  Brain,
  Check,
  ChevronDown,
  CircleStop,
  Database,
  FileCode2,
  FlaskConical,
  Globe2,
  HardDrive,
  Menu,
  MessageSquare,
  Pencil,
  Plus,
  Search,
  Settings as SettingsIcon,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  WifiOff,
  X,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import { harnessClient, type HarnessClient } from "./client";
import { PendingQueue } from "./components/PendingQueue";
import { ToolCallCard } from "./components/ToolCallCard";
import type {
  AgUiEvent,
  AppArea,
  ChatMessage,
  ChatSnapshot,
  EvalPhase,
  EvalReport,
  EvalRun,
  EvalTier,
  EvalsSnapshot,
  FeedbackRecord,
  Grant,
  JsonValue,
  PendingConfirmation,
  SessionStatus,
  SettingsSnapshot,
  SetupStatus,
  ToolCall,
  WorkspaceGroup,
} from "./types";

interface AppProps {
  client?: HarnessClient;
}

interface AppData {
  chat: ChatSnapshot;
  evals: EvalsSnapshot;
  settings: SettingsSnapshot;
}

interface LiveRun {
  runId: string;
  status: "queued" | "running" | "finished" | "error";
  reasoning: string;
  content: string;
  tools: ToolCall[];
  toolArguments: Record<string, string>;
  steps: number;
  outcome?: { kind: string; reasonCode: string };
  error?: string;
}

const areaItems = [
  { id: "chat" as const, label: "Chat", icon: MessageSquare },
  { id: "evals" as const, label: "Evals", icon: FlaskConical },
  { id: "settings" as const, label: "Settings", icon: SettingsIcon },
];

export function App({ client = harnessClient }: AppProps) {
  const [area, setArea] = useState<AppArea>("chat");
  const [data, setData] = useState<AppData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null);
  const [session, setSession] = useState<SessionStatus | null>(null);

  // Setup comes first: on an unconfigured host every other endpoint is useless,
  // and a host behind a password answers nothing else until the Operator logs in.
  useEffect(() => {
    let active = true;
    setLoadError(null);

    client
      .getSetupStatus()
      .then(async (status) => {
        if (!active) return;
        setSetupStatus(status);
        if (status.required) return;
        const current = await client.getSessionStatus();
        if (active) setSession(current);
      })
      .catch((error: unknown) => {
        if (active) setLoadError(errorMessage(error));
      });

    return () => {
      active = false;
    };
  }, [client]);

  const gateOpen =
    setupStatus !== null &&
    !setupStatus.required &&
    session !== null &&
    (!session.authentication_required || session.authenticated);

  useEffect(() => {
    if (!gateOpen || data !== null) return;
    let active = true;

    Promise.all([
      client.getChatSnapshot(),
      client.getEvalsSnapshot(),
      client.getSettingsSnapshot(),
    ])
      .then(([chat, evals, settings]) => {
        if (active) setData({ chat, evals, settings });
      })
      .catch((error: unknown) => {
        if (active) setLoadError(errorMessage(error));
      });

    return () => {
      active = false;
    };
  }, [client, gateOpen, data]);

  if (setupStatus?.required) {
    return <SetupArea client={client} status={setupStatus} onDone={setSetupStatus} />;
  }

  if (session?.authentication_required && !session.authenticated) {
    return <LoginArea client={client} onAuthenticated={setSession} />;
  }

  if (setupStatus?.restart_required) {
    return (
      <div className="setup-page">
        <div className="setup-card" role="status">
          <header>
            <span className="loading-mark">H2</span>
            <h1>Configuração gravada</h1>
            <p>Reinicie o servidor para que ela entre em vigor e recarregue esta página.</p>
          </header>
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="primary-nav" aria-label="Áreas principais">
        <button
          className="brand-mark"
          type="button"
          onClick={() => setArea("chat")}
          aria-label="Harness 2.0"
        >
          H<span>2</span>
        </button>
        <nav>
          {areaItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={area === item.id ? "active" : ""}
                type="button"
                key={item.id}
                onClick={() => setArea(item.id)}
                aria-current={area === item.id ? "page" : undefined}
              >
                <Icon size={20} strokeWidth={1.8} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="primary-nav-footer">
          <span className="avatar" aria-label="Operator local">
            OP
          </span>
        </div>
      </aside>

      <main className="area-stage">
        {loadError ? (
          <LoadError message={loadError} />
        ) : !data ? (
          <LoadingShell />
        ) : (
          <>
            {area === "chat" && (
              <ChatArea
                snapshot={data.chat}
                client={client}
                onSnapshot={(chat) => setData((current) => current && { ...current, chat })}
              />
            )}
            {area === "evals" && (
              <EvalsArea
                snapshot={data.evals}
                client={client}
                onSnapshot={(evals) =>
                  setData((current) => current && { ...current, evals })
                }
              />
            )}
            {area === "settings" && (
              <SettingsArea snapshot={data.settings} clientMode={client.mode} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

function LoginArea({
  client,
  onAuthenticated,
}: {
  client: HarnessClient;
  onAuthenticated: (status: SessionStatus) => void;
}) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    void client
      .login(password)
      .then(onAuthenticated)
      .catch((cause: unknown) => setError(errorMessage(cause)))
      .finally(() => {
        setBusy(false);
        setPassword("");
      });
  };

  return (
    <div className="setup-page">
      <form className="setup-card" onSubmit={submit}>
        <header>
          <span className="loading-mark">H2</span>
          <h1>Entrar no Harness</h1>
          <p>
            Este host exige a senha de Operator. A sessão vive só nesta aba: recarregar
            a página pede a senha de novo.
          </p>
        </header>

        <label className="setup-field">
          <span>Senha de Operator</span>
          <input
            type="password"
            value={password}
            required
            aria-label="Senha de Operator"
            autoComplete="current-password"
            onChange={(event) => setPassword(event.target.value)}
          />
        </label>

        {error && (
          <div className="action-error setup-error" role="alert">
            {error}
          </div>
        )}

        <button className="primary-button" type="submit" disabled={busy}>
          {busy ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}

const setupFields = [
  {
    name: "allowed_workspace_roots",
    label: "Raízes de Workspace autorizadas",
    hint: "Caminhos absolutos, um por linha. Nada fora daqui é legível ou gravável.",
    multiline: true,
    required: true,
  },
  {
    name: "state_dir",
    label: "Diretório de estado",
    hint: "Onde ficam o CanonicalHistory, a telemetria e o tokenizer.",
    multiline: false,
    required: true,
  },
  {
    name: "tokenizer_path",
    label: "Caminho do tokenizer.json",
    hint: "Arquivo HuggingFace usado para o orçamento de contexto.",
    multiline: false,
    required: true,
  },
  {
    name: "tokenizer_digest",
    label: "SHA-256 do tokenizer",
    hint: "sha256sum do arquivo acima. O harness recusa qualquer outro conteúdo.",
    multiline: false,
    required: true,
  },
  {
    name: "allowed_origins",
    label: "Origins autorizadas",
    hint: "Uma por linha, por exemplo http://127.0.0.1:8765.",
    multiline: true,
    required: true,
  },
  {
    name: "brave_api_key",
    label: "Chave da Brave Search (opcional)",
    hint: "Sem ela, web_search não é oferecida ao modelo.",
    multiline: false,
    required: false,
  },
] as const;

function SetupArea({
  client,
  status,
  onDone,
}: {
  client: HarnessClient;
  status: SetupStatus;
  onDone: (status: SetupStatus) => void;
}) {
  const [token, setToken] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const lines = (name: string) =>
    (values[name] ?? "")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const brave = (values.brave_api_key ?? "").trim();
    void client
      .completeSetup(token.trim(), {
        allowed_workspace_roots: lines("allowed_workspace_roots"),
        state_dir: (values.state_dir ?? "").trim(),
        tokenizer_path: (values.tokenizer_path ?? "").trim(),
        tokenizer_digest: (values.tokenizer_digest ?? "").trim(),
        allowed_origins: lines("allowed_origins"),
        brave_api_key: brave === "" ? null : brave,
      })
      .then(() => onDone({ ...status, configured: true, required: false, restart_required: true }))
      .catch((cause: unknown) => setError(errorMessage(cause)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="setup-page">
      <form className="setup-card" onSubmit={submit}>
        <header>
          <span className="loading-mark">H2</span>
          <h1>Configurar este host</h1>
          <p>
            O servidor imprimiu um token de setup no stderr ao subir. Ele expira, vale uma
            vez e só é aceito por uma conexão direta de loopback.
          </p>
        </header>

        <label className="setup-field">
          <span>Token de setup</span>
          <input
            type="password"
            value={token}
            required
            aria-label="Token de setup"
            autoComplete="off"
            onChange={(event) => setToken(event.target.value)}
          />
          <small>Copie a linha "Harness setup: /setup token=…" do terminal.</small>
        </label>

        {setupFields.map((field) => (
          <label className="setup-field" key={field.name}>
            <span>{field.label}</span>
            {field.multiline ? (
              <textarea
                rows={3}
                required={field.required}
                aria-label={field.label}
                value={values[field.name] ?? ""}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [field.name]: event.target.value }))
                }
              />
            ) : (
              <input
                type={field.name === "brave_api_key" ? "password" : "text"}
                required={field.required}
                aria-label={field.label}
                autoComplete="off"
                value={values[field.name] ?? ""}
                onChange={(event) =>
                  setValues((current) => ({ ...current, [field.name]: event.target.value }))
                }
              />
            )}
            <small>{field.hint}</small>
          </label>
        ))}

        {error && (
          <div className="action-error setup-error" role="alert">
            {error}
          </div>
        )}

        <button className="primary-button" type="submit" disabled={busy}>
          {busy ? "Gravando…" : "Concluir setup"}
        </button>
        <p className="setup-footnote">
          Depois de concluir, reinicie o servidor para que a configuração entre em vigor.
        </p>
      </form>
    </div>
  );
}

function MockPill({ mode }: { mode: HarnessClient["mode"] }) {
  if (mode === "live") return null;
  return (
    <span className="preview-pill preview-pill--mock">
      <Database size={12} /> Preview · dados simulados
    </span>
  );
}

function LoadingShell() {
  return (
    <div className="loading-shell" role="status" aria-live="polite">
      <span className="loading-mark">H2</span>
      <p>Carregando o Harness…</p>
    </div>
  );
}

function LoadError({ message }: { message: string }) {
  return (
    <div className="load-error" role="alert">
      <WifiOff size={26} />
      <h1>Não foi possível carregar o Harness</h1>
      <p>{message}</p>
    </div>
  );
}

function ChatArea({
  snapshot,
  client,
  onSnapshot,
}: {
  snapshot: ChatSnapshot;
  client: HarnessClient;
  onSnapshot: (snapshot: ChatSnapshot) => void;
}) {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [liveRuns, setLiveRuns] = useState<Record<string, LiveRun>>({});
  const controllers = useRef(new Set<AbortController>());
  const snapshotRequest = useRef(0);

  useEffect(
    () => () => {
      for (const controller of controllers.current) controller.abort();
      controllers.current.clear();
    },
    [],
  );

  const replaceSnapshot = async (load: () => Promise<ChatSnapshot>) => {
    const request = ++snapshotRequest.current;
    const next = await load();
    if (request === snapshotRequest.current) onSnapshot(next);
    return next;
  };

  const refresh = (conversationId = snapshot.conversationId) =>
    replaceSnapshot(() => client.getChatSnapshot(conversationId));

  const runAction = async (name: string, action: () => Promise<void>) => {
    setBusyAction(name);
    setActionError(null);
    try {
      await action();
      return true;
    } catch (error) {
      setActionError(errorMessage(error));
      return false;
    } finally {
      setBusyAction(null);
    }
  };

  const selectConversation = (conversationId: string) => {
    for (const controller of controllers.current) controller.abort();
    controllers.current.clear();
    setLiveRuns({});
    void runAction("select", async () => {
      await replaceSnapshot(() => client.selectConversation(conversationId));
      setSidebarOpen(false);
    });
  };

  const createConversation = async (root: string, name: string) => {
    return runAction("create", async () => {
      const conversation = await client.createConversation(root, name);
      await replaceSnapshot(() => client.selectConversation(conversation.id));
      setSidebarOpen(false);
    });
  };

  const renameConversation = () => {
    if (!snapshot.conversationId || !snapshot.conversationTitle) return;
    const name = window.prompt("Novo nome da Conversation", snapshot.conversationTitle)?.trim();
    if (!name || name === snapshot.conversationTitle) return;
    void runAction("rename", async () => {
      await client.renameConversation(snapshot.conversationId!, name);
      await refresh();
    });
  };

  const archiveConversation = () => {
    if (!snapshot.conversationId) return;
    void runAction("archive", async () => {
      await client.archiveConversation(snapshot.conversationId!);
      await replaceSnapshot(() => client.getChatSnapshot(null));
    });
  };

  const deleteConversation = () => {
    if (!snapshot.conversationId) return;
    if (!window.confirm("Excluir esta Conversation permanentemente?")) return;
    void runAction("delete", async () => {
      await client.deleteConversation(snapshot.conversationId!);
      await replaceSnapshot(() => client.getChatSnapshot(null));
    });
  };

  const toggleGrant = (permission: "WriteGrant" | "WebAccessGrant") => {
    if (!snapshot.conversationId) return;
    const current = snapshot.grants.find((grant) => grant.permission === permission);
    void runAction(`grant-${permission}`, async () => {
      if (current) await client.revokeGrant(snapshot.conversationId!, current.id);
      else await client.addGrant(snapshot.conversationId!, permission);
      await refresh();
    });
  };

  const send = (content: string) => {
    if (!snapshot.conversationId) return;
    const conversationId = snapshot.conversationId;
    const runId = makeId();
    const controller = new AbortController();
    controllers.current.add(controller);
    setActionError(null);
    setLiveRuns((current) => ({
      ...current,
      [runId]: {
        runId,
        status: "queued",
        reasoning: "",
        content: "",
        tools: [],
        toolArguments: {},
        steps: 0,
      },
    }));

    const onEvent = (event: AgUiEvent) => {
      setLiveRuns((current) => {
        const run = current[runId];
        if (!run) return current;
        return { ...current, [runId]: reduceLiveRun(run, event) };
      });
      const isConfirmationEvent =
        event.type === "CUSTOM" &&
        (event.name === "harness.confirmation_required" ||
          event.name === "harness.confirmation_resolved");
      if (
        event.type === "RUN_STARTED" ||
        event.type === "STEP_STARTED" ||
        isConfirmationEvent
      ) {
        void refresh(conversationId).catch((error) => setActionError(errorMessage(error)));
      }
    };

    void client
      .streamAgent(
        {
          threadId: conversationId,
          runId,
          messages: [{ id: `user-${runId}`, role: "user", content }],
        },
        onEvent,
        controller.signal,
      )
      .then(async (terminal) => {
        if (terminal.type === "RUN_ERROR") setActionError(terminal.message);
        await refresh(conversationId);
        setLiveRuns((current) => {
          const next = { ...current };
          delete next[runId];
          return next;
        });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const message = errorMessage(error);
        setActionError(message);
        setLiveRuns((current) => {
          const run = current[runId];
          return run
            ? { ...current, [runId]: { ...run, status: "error", error: message } }
            : current;
        });
      })
      .finally(() => controllers.current.delete(controller));
  };

  const editPending = async (requestId: string, content: string) => {
    if (!snapshot.conversationId) return;
    await client.editPendingRequest(snapshot.conversationId, requestId, content);
    await refresh();
  };

  const cancelPending = async (requestId: string) => {
    if (!snapshot.conversationId) return;
    await client.cancelPendingRequest(snapshot.conversationId, requestId);
    await refresh();
  };

  const stop = () => {
    if (!snapshot.conversationId) return;
    void runAction("stop", async () => {
      await client.stop(snapshot.conversationId!);
      await refresh();
    });
  };

  const resolveConfirmation = (approved: boolean) => {
    const pending = snapshot.pendingConfirmation;
    if (!snapshot.conversationId || !pending) return;
    void runAction(approved ? "confirm-approve" : "confirm-deny", async () => {
      await client.resolveConfirmation(snapshot.conversationId!, pending.id, approved);
      await refresh();
    });
  };

  const liveMessages = Object.values(liveRuns)
    .filter((run) => run.content || run.reasoning || run.tools.length || run.error)
    .map(liveRunMessage);
  const isRunning =
    snapshot.activeTurn !== null ||
    Object.values(liveRuns).some(
      (run) => run.status === "queued" || run.status === "running",
    );
  const hasWorkspaces = snapshot.workspaces.length > 0;
  const hasConversation = snapshot.conversationId !== null;

  return (
    <div className="chat-layout">
      {sidebarOpen && (
        <button
          className="drawer-scrim"
          type="button"
          onClick={() => setSidebarOpen(false)}
          aria-label="Fechar conversas"
        />
      )}
      <ConversationSidebar
        groups={snapshot.workspaces}
        selectedId={snapshot.conversationId}
        open={sidebarOpen}
        busy={busyAction === "create" || busyAction === "select"}
        onClose={() => setSidebarOpen(false)}
        onSelect={selectConversation}
        onCreate={createConversation}
      />

      <section className="chat-stage" aria-labelledby="conversation-title">
        <header className="conversation-header">
          <button
            className="icon-button mobile-menu"
            type="button"
            onClick={() => setSidebarOpen(true)}
            aria-label="Abrir conversas"
            aria-expanded={sidebarOpen}
          >
            <Menu size={18} />
          </button>
          <div className="conversation-identity">
            <span className="eyebrow">{snapshot.workspaceName ?? "Harness"}</span>
            <h1 id="conversation-title">
              {snapshot.conversationTitle ??
                (hasWorkspaces ? "Nenhuma Conversation" : "Workspace não autorizado")}
            </h1>
          </div>
          {hasConversation && (
            <>
              <GrantChips
                grants={snapshot.grants}
                busyAction={busyAction}
                onToggle={toggleGrant}
              />
              <div className="conversation-actions" aria-label="Ações da Conversation">
                <button
                  className="icon-button"
                  type="button"
                  onClick={renameConversation}
                  disabled={busyAction !== null}
                  aria-label="Renomear Conversation"
                >
                  <Pencil size={14} />
                </button>
                <button
                  className="icon-button"
                  type="button"
                  onClick={archiveConversation}
                  disabled={busyAction !== null}
                  aria-label="Arquivar Conversation"
                >
                  <Archive size={14} />
                </button>
                <button
                  className="icon-button icon-button--danger"
                  type="button"
                  onClick={deleteConversation}
                  disabled={busyAction !== null}
                  aria-label="Excluir Conversation"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </>
          )}
          <MockPill mode={client.mode} />
        </header>

        {actionError && (
          <div className="action-error" role="alert">
            {actionError}
          </div>
        )}

        {!hasWorkspaces ? (
          <EmptyState
            title="Nenhuma raiz de Workspace autorizada"
            detail="Configure HARNESS_WORKSPACE_ROOTS no servidor para iniciar uma Conversation."
          />
        ) : !hasConversation ? (
          <EmptyState
            title="Crie a primeira Conversation"
            detail="Escolha uma das raízes autorizadas no painel de Conversations."
            action={() => setSidebarOpen(true)}
          />
        ) : (
          <>
            <div className="timeline" id="chat-timeline">
              {isRunning && (
                <div className="run-strip" role="status">
                  <span className="pulse-dot" />
                  <span>
                    {snapshot.activeTurn ? "Turn em execução" : "Aguardando na fila"}
                  </span>
                  <span>{Object.keys(liveRuns).length} stream(s)</span>
                </div>
              )}
              {!snapshot.messages.length && !liveMessages.length && (
                <div className="timeline-empty">
                  <MessageSquare size={21} />
                  <h2>Conversation vazia</h2>
                  <p>Envie uma solicitação para iniciar o CanonicalHistory.</p>
                </div>
              )}
              {[...snapshot.messages, ...liveMessages].map((message) => (
                <TimelineMessage
                  message={message}
                  key={message.id}
                  conversationId={snapshot.conversationId!}
                  client={client}
                  existingFeedback={snapshot.feedback.find(
                    (item) => item.turn_id === message.turnId,
                  )}
                />
              ))}
              <PendingQueue
                requests={snapshot.pendingRequests}
                onEdit={editPending}
                onCancel={cancelPending}
              />
              {snapshot.pendingConfirmation && (
                <ConfirmationCard
                  confirmation={snapshot.pendingConfirmation}
                  busyAction={busyAction}
                  onResolve={resolveConfirmation}
                />
              )}
            </div>
            <Composer
              isRunning={isRunning}
              isStopping={busyAction === "stop"}
              onStop={stop}
              onSend={send}
            />
          </>
        )}
      </section>

      <ChatContext snapshot={snapshot} />
    </div>
  );
}

function ConversationSidebar({
  groups,
  selectedId,
  open,
  busy,
  onClose,
  onSelect,
  onCreate,
}: {
  groups: WorkspaceGroup[];
  selectedId: string | null;
  open: boolean;
  busy: boolean;
  onClose: () => void;
  onSelect: (id: string) => void;
  onCreate: (root: string, name: string) => Promise<boolean>;
}) {
  const [creating, setCreating] = useState(false);
  const [root, setRoot] = useState(groups[0]?.root ?? "");
  const [name, setName] = useState("New conversation");
  const [search, setSearch] = useState("");

  useEffect(() => {
    if (!groups.some((group) => group.root === root)) setRoot(groups[0]?.root ?? "");
  }, [groups, root]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!root || !name.trim()) return;
    void onCreate(root, name.trim()).then((created) => {
      if (created) setCreating(false);
    });
  };

  const normalizedSearch = search.trim().toLocaleLowerCase("pt-BR");

  return (
    <aside
      className={`conversation-sidebar ${open ? "open" : ""}`}
      aria-label="Conversas por Workspace"
    >
      <div className="sidebar-topline">
        <div>
          <span className="eyebrow">Harness</span>
          <strong>Conversations</strong>
        </div>
        <button
          className="icon-button close-drawer"
          type="button"
          onClick={onClose}
          aria-label="Fechar conversas"
        >
          <X size={17} />
        </button>
      </div>
      <button
        className="new-chat-button"
        type="button"
        disabled={!groups.length || busy}
        onClick={() => setCreating((current) => !current)}
        aria-expanded={creating}
      >
        <Plus size={16} /> Nova Conversation
      </button>
      {creating && (
        <form className="new-chat-form" onSubmit={submit}>
          <label>
            <span>Raiz autorizada</span>
            <select
              value={root}
              onChange={(event) => setRoot(event.target.value)}
              aria-label="Raiz autorizada"
            >
              {groups.map((group) => (
                <option value={group.root} key={group.id}>
                  {group.root}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Nome</span>
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <button className="primary-button" type="submit" disabled={busy || !name.trim()}>
            Criar
          </button>
        </form>
      )}
      <label className="sidebar-search">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">Buscar Conversations</span>
        <input
          type="search"
          placeholder="Buscar"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      <div className="workspace-groups">
        {groups.map((group) => {
          const conversations = group.conversations.filter((conversation) =>
            conversation.title.toLocaleLowerCase("pt-BR").includes(normalizedSearch),
          );
          return (
            <section className="workspace-group" key={group.id}>
              <div className="workspace-heading">
                <ChevronDown size={13} />
                <h2>{group.name}</h2>
                <span>{conversations.length}</span>
              </div>
              <div className="conversation-list">
                {conversations.map((conversation) => (
                  <button
                    className={conversation.id === selectedId ? "active" : ""}
                    type="button"
                    key={conversation.id}
                    onClick={() => onSelect(conversation.id)}
                    aria-current={conversation.id === selectedId ? "page" : undefined}
                  >
                    <span className="conversation-name">{conversation.title}</span>
                    <time>{formatTimestamp(conversation.updatedAt)}</time>
                  </button>
                ))}
              </div>
            </section>
          );
        })}
      </div>
      <div className="sidebar-footer-note">
        <ShieldCheck size={14} />
        WorkspaceRootGrant define o limite local
      </div>
    </aside>
  );
}

function GrantChips({
  grants,
  busyAction,
  onToggle,
}: {
  grants: Grant[];
  busyAction: string | null;
  onToggle: (permission: "WriteGrant" | "WebAccessGrant") => void;
}) {
  const root = grants.find((grant) => grant.permission === "WorkspaceRootGrant");
  return (
    <div className="permission-chips" aria-label="Grants da Conversation">
      {root && (
        <span className="permission permission--active" title={root.scope}>
          <Search size={12} /> Read
        </span>
      )}
      {(
        [
          ["WriteGrant", "Write", FileCode2],
          ["WebAccessGrant", "Web", Globe2],
        ] as const
      ).map(([permission, label, Icon]) => {
        const active = grants.some((grant) => grant.permission === permission);
        return (
          <button
            type="button"
            className={`permission permission--${label} ${active ? "permission--active" : ""}`}
            key={permission}
            onClick={() => onToggle(permission)}
            disabled={busyAction === `grant-${permission}`}
            aria-pressed={active}
            aria-label={`${active ? "Revogar" : "Ativar"} ${label}`}
          >
            <Icon size={12} /> {label}
          </button>
        );
      })}
    </div>
  );
}

function ConfirmationCard({
  confirmation,
  busyAction,
  onResolve,
}: {
  confirmation: PendingConfirmation;
  busyAction: string | null;
  onResolve: (approved: boolean) => void;
}) {
  const busy = busyAction === "confirm-approve" || busyAction === "confirm-deny";
  return (
    <section className="confirmation-card" role="alertdialog" aria-labelledby="confirmation-title">
      <header>
        <ShieldAlert size={16} />
        <h2 id="confirmation-title">Escrita com dado da web</h2>
      </header>
      <p>
        O Turn leu conteúdo da web e agora quer escrever no Workspace. Aprovar vale só
        para esta chamada: não cria grant nem amplia acesso.
      </p>
      <ul className="confirmation-calls">
        {confirmation.tool_calls.map((call) => (
          <li key={call.id}>
            <code>{call.name}</code>
            <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
          </li>
        ))}
      </ul>
      <div className="confirmation-actions">
        <button
          type="button"
          className="secondary-button"
          onClick={() => onResolve(false)}
          disabled={busy}
        >
          Negar
        </button>
        <button
          type="button"
          className="primary-button"
          onClick={() => onResolve(true)}
          disabled={busy}
        >
          Aprovar esta escrita
        </button>
      </div>
    </section>
  );
}

function TimelineMessage({
  message,
  conversationId,
  client,
  existingFeedback,
}: {
  message: ChatMessage;
  conversationId: string;
  client: HarnessClient;
  existingFeedback?: FeedbackRecord;
}) {
  if (message.role === "user") {
    return (
      <article className="message message--user">
        <div className="message-meta">
          <span>Operator</span>
          <time>{formatTimestamp(message.createdAt)}</time>
        </div>
        <p>{message.content}</p>
      </article>
    );
  }

  return (
    <article className="message message--assistant">
      <div className="assistant-marker" aria-hidden="true">
        <Sparkles size={16} />
      </div>
      <div className="assistant-content">
        <div className="message-meta">
          <span>Harness</span>
          <time>{formatTimestamp(message.createdAt)}</time>
          {message.live && <em className="live-label">live</em>}
        </div>
        {message.reasoning && (
          <details className="reasoning-block">
            <summary>
              <Brain size={14} />
              <span>{message.reasoning.summary}</span>
              <em>transitório</em>
              <ChevronDown size={14} className="summary-chevron" />
            </summary>
            <p>{message.reasoning.content}</p>
          </details>
        )}
        {message.tools && message.tools.length > 0 && (
          <div className="tool-stack" aria-label="Chamadas de tools">
            {message.tools.map((tool) => (
              <ToolCallCard tool={tool} key={tool.id} />
            ))}
          </div>
        )}
        {message.events?.map((event) => (
          <details className="canonical-event" key={event.id}>
            <summary>
              {event.kind} <ChevronDown size={14} className="summary-chevron" />
            </summary>
            <pre>{JSON.stringify(event.payload, null, 2)}</pre>
          </details>
        ))}
        {message.content && (
          <div className="assistant-answer">
            <p>{message.content}</p>
          </div>
        )}
        {message.metrics && <Metrics metrics={message.metrics} />}
        {message.terminalOutcome && (
          <div className={`turn-outcome turn-outcome--${message.terminalOutcome.kind}`}>
            <span>{message.terminalOutcome.kind}</span>
            <code>{message.terminalOutcome.reason_code}</code>
          </div>
        )}
        {message.liveOutcome && (
          <div className={`turn-outcome turn-outcome--${message.liveOutcome.kind}`}>
            <span>{message.liveOutcome.kind}</span>
            <code>{message.liveOutcome.reasonCode}</code>
          </div>
        )}
        {!message.live && message.content && (
          <Feedback
            conversationId={conversationId}
            turnId={message.turnId}
            client={client}
            initial={existingFeedback}
          />
        )}
      </div>
    </article>
  );
}

function Metrics({ metrics }: { metrics: Record<string, string | number> }) {
  return (
    <details className="metrics-block">
      <summary>
        <Activity size={14} /> Métricas fornecidas
        <ChevronDown className="summary-chevron" size={14} />
      </summary>
      <div className="metrics-grid">
        {Object.entries(metrics).map(([label, value]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
    </details>
  );
}

function Feedback({
  conversationId,
  turnId,
  client,
  initial,
}: {
  conversationId: string;
  turnId: string;
  client: HarnessClient;
  initial?: FeedbackRecord;
}) {
  const [feedback, setFeedback] = useState<FeedbackRecord | undefined>(initial);
  const [draftId, setDraftId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const rate = (rating: -1 | 1) => {
    setBusy(true);
    setError(null);
    void client
      .addFeedback(conversationId, rating, turnId)
      .then(setFeedback)
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setBusy(false));
  };

  const exportDraft = () => {
    if (!feedback) return;
    setBusy(true);
    setError(null);
    void client
      .createRegressionDraft(conversationId, feedback.id)
      .then((draft) => setDraftId(draft.id))
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="feedback-row" aria-label="Feedback da resposta">
      <span>
        {draftId
          ? `Draft ${draftId} criado`
          : feedback
            ? "Feedback registrado"
            : "Esta resposta ajudou?"}
      </span>
      <button
        className={feedback?.rating === 1 ? "active" : ""}
        type="button"
        onClick={() => rate(1)}
        disabled={busy}
        aria-pressed={feedback?.rating === 1}
      >
        <ThumbsUp size={13} /> Funcionou
      </button>
      <button
        className={feedback?.rating === -1 ? "active negative" : ""}
        type="button"
        onClick={() => rate(-1)}
        disabled={busy}
        aria-pressed={feedback?.rating === -1}
      >
        <ThumbsDown size={13} /> Não funcionou
      </button>
      {feedback?.rating === -1 && !draftId && (
        <button type="button" onClick={exportDraft} disabled={busy}>
          Exportar draft
        </button>
      )}
      {error && <span className="inline-error" role="alert">{error}</span>}
    </div>
  );
}

function Composer({
  isRunning,
  isStopping,
  onStop,
  onSend,
}: {
  isRunning: boolean;
  isStopping: boolean;
  onStop: () => void;
  onSend: (prompt: string) => void;
}) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const insert = (text: string) => {
    const textarea = textareaRef.current;
    if (!textarea) return setValue((current) => `${current}${text}`);
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    setValue((current) => `${current.slice(0, start)}${text}${current.slice(end)}`);
    requestAnimationFrame(() => {
      textarea.focus();
      textarea.setSelectionRange(start + text.length, start + text.length);
    });
  };

  const submit = () => {
    const prompt = value.trim();
    if (!prompt) return;
    onSend(prompt);
    setValue("");
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="composer-dock">
      <form className="composer" onSubmit={onSubmit}>
        <label className="sr-only" htmlFor="prompt-composer">
          Solicitação para o Harness
        </label>
        <textarea
          id="prompt-composer"
          ref={textareaRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          placeholder="Peça uma mudança ou faça uma pergunta…"
        />
        <div className="composer-footer">
          <div className="composer-tools">
            <button type="button" onClick={() => insert("@file:")}>
              <AtSign size={15} /> arquivo
            </button>
            <button type="button" onClick={() => insert("```diff\n\n```")}>
              <FileCode2 size={15} /> diff
            </button>
          </div>
          <div className="composer-actions">
            <span>⌘ Enter</span>
            <button
              className="stop-button"
              type="button"
              onClick={onStop}
              disabled={!isRunning || isStopping}
            >
              <Square size={13} fill="currentColor" /> Stop
            </button>
            <button
              className="send-button"
              type="submit"
              disabled={!value.trim()}
              aria-label="Enviar solicitação"
            >
              <ArrowUp size={17} />
            </button>
          </div>
        </div>
      </form>
      <p>Solicitações concorrentes permanecem na fila desta Conversation.</p>
    </div>
  );
}

function EmptyState({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: () => void;
}) {
  return (
    <div className="empty-transcript">
      <MessageSquare size={24} />
      <h2>{title}</h2>
      <p>{detail}</p>
      {action && (
        <button className="secondary-button" type="button" onClick={action}>
          Nova Conversation
        </button>
      )}
    </div>
  );
}

function ChatContext({ snapshot }: { snapshot: ChatSnapshot }) {
  if (!snapshot.execution) return <aside className="chat-context" />;
  return (
    <aside className="chat-context" aria-label="ExecutionRoute">
      <div className="context-heading">
        <span className="eyebrow">ExecutionRoute</span>
        <span className="context-live">snapshot</span>
      </div>
      <div className="context-model">
        <span className="context-model-icon">
          <Bot size={17} />
        </span>
        <div>
          <strong>{snapshot.execution.runtimeProfile}</strong>
          <span>{snapshot.execution.defaultExecutionRoute}</span>
        </div>
      </div>
      <dl className="context-facts">
        {Object.entries(snapshot.execution.loop).map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{displayValue(value)}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

function EvalsArea({
  snapshot,
  client,
  onSnapshot,
}: {
  snapshot: EvalsSnapshot;
  client: HarnessClient;
  onSnapshot: (snapshot: EvalsSnapshot) => void;
}) {
  const [selectedRunId, setSelectedRunId] = useState(snapshot.selectedRunId);
  const [showCreate, setShowCreate] = useState(false);
  const [experimentId, setExperimentId] = useState(snapshot.experiments[0]?.id ?? "");
  const [tier, setTier] = useState<EvalTier>("experiment");
  const [phase, setPhase] = useState<EvalPhase>("pilot");
  const [exportedReport, setExportedReport] = useState<EvalReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (selectedRunId && snapshot.runs.some((run) => run.id === selectedRunId)) return;
    setSelectedRunId(snapshot.selectedRunId ?? snapshot.runs[0]?.id ?? null);
  }, [selectedRunId, snapshot.runs, snapshot.selectedRunId]);

  const hasActiveRuns = snapshot.runs.some((run) =>
    ["queued", "running", "canceling"].includes(run.status),
  );

  useEffect(() => {
    if (!hasActiveRuns) return;
    let active = true;
    const interval = window.setInterval(() => {
      void client
        .getEvalsSnapshot()
        .then((next) => {
          if (active) onSnapshot({ ...next, selectedRunId: selectedRunId ?? next.selectedRunId });
        })
        .catch((caught) => {
          if (active) setError(errorMessage(caught));
        });
    }, 2_000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [client, hasActiveRuns, onSnapshot, selectedRunId]);

  const refresh = async (preferredRunId?: string) => {
    const next = await client.getEvalsSnapshot();
    const selected = preferredRunId ?? selectedRunId ?? next.selectedRunId;
    onSnapshot({ ...next, selectedRunId: selected });
    setSelectedRunId(selected);
  };

  const act = (action: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    void action()
      .catch((caught) => setError(errorMessage(caught)))
      .finally(() => setBusy(false));
  };

  const createRun = (event: FormEvent) => {
    event.preventDefault();
    if (!experimentId) return;
    act(async () => {
      const created = await client.createEvalRun({ experimentId, tier, phase });
      const started = await client.startEvalRun(created.id);
      await refresh(started.id);
      setShowCreate(false);
    });
  };

  const selectedRun = snapshot.runs.find((run) => run.id === selectedRunId);
  const report =
    exportedReport?.run_id === selectedRunId
      ? exportedReport
      : snapshot.reports.find((item) => item.run_id === selectedRunId);

  return (
    <div className="evals-page">
      <header className="area-header">
        <div>
          <span className="eyebrow">Evaluation lab</span>
          <h1>Evals</h1>
          <p>Runs e relatórios retornados pelo serviço de avaliação.</p>
        </div>
        <div className="area-header-actions">
          <MockPill mode={client.mode} />
          <button
            className="primary-button"
            type="button"
            onClick={() => setShowCreate((current) => !current)}
            disabled={!snapshot.capabilities.eval_runner || !snapshot.experiments.length}
          >
            <Plus size={15} /> Novo run
          </button>
        </div>
      </header>

      {!snapshot.capabilities.eval_runner && (
        <div className="blocked-banner" role="status">
          <CircleStop size={15} /> blocked: eval runner indisponível
        </div>
      )}
      {error && <div className="page-error" role="alert">{error}</div>}
      {showCreate && (
        <form className="eval-run-form" onSubmit={createRun}>
          <label>
            <span>Experiment</span>
            <select value={experimentId} onChange={(event) => setExperimentId(event.target.value)}>
              {snapshot.experiments.map((experiment) => (
                <option value={experiment.id} key={experiment.id}>{experiment.id}</option>
              ))}
            </select>
          </label>
          <label>
            <span>Tier</span>
            <select value={tier} onChange={(event) => setTier(event.target.value as EvalTier)}>
              <option value="contract">contract</option>
              <option value="model_smoke">model_smoke</option>
              <option value="experiment">experiment</option>
            </select>
          </label>
          <label>
            <span>Phase</span>
            <select value={phase} onChange={(event) => setPhase(event.target.value as EvalPhase)}>
              <option value="pilot">pilot</option>
              <option value="promotion">promotion</option>
            </select>
          </label>
          <button className="primary-button" type="submit" disabled={busy}>Criar e iniciar</button>
        </form>
      )}

      <div className="evals-layout">
        <aside className="experiment-panel" aria-label="Experimentos e runs">
          <div className="panel-title">
            <div>
              <span className="eyebrow">Registry</span>
              <h2>Experimentos</h2>
            </div>
            <FlaskConical size={16} />
          </div>
          <div className="experiment-list">
            {snapshot.experiments.map((experiment) => {
              const runs = snapshot.runs.filter(
                (run) => run.experiment_id === experiment.id,
              );
              return (
                <section className="experiment-item" key={experiment.id}>
                  <div className="experiment-heading">
                    <span className={`status-dot status-dot--${experiment.status}`} />
                    <div>
                      <h3>{experiment.id}</h3>
                      <p>{experiment.runtime_profile} · {experiment.execution_route}</p>
                    </div>
                  </div>
                  <div className="run-list">
                    {runs.map((run) => (
                      <button
                        type="button"
                        className={run.id === selectedRunId ? "active" : ""}
                        key={run.id}
                        onClick={() => {
                          setSelectedRunId(run.id);
                          setExportedReport(null);
                        }}
                      >
                        <span className={`run-state run-state--${runVisualStatus(run)}`}>
                          {run.status === "completed" ? (
                            <Check size={12} />
                          ) : run.status === "running" || run.status === "queued" ? (
                            <Activity size={12} />
                          ) : (
                            <CircleStop size={12} />
                          )}
                        </span>
                        <span>
                          <strong>{run.phase} · {run.tier}</strong>
                          <small>{run.status} · {formatTimestamp(run.updated_at)}</small>
                        </span>
                      </button>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        </aside>

        {selectedRun ? (
          <EvalRunView
            run={selectedRun}
            report={report}
            busy={busy}
            onStart={() =>
              act(async () => {
                await client.startEvalRun(selectedRun.id);
                await refresh(selectedRun.id);
              })
            }
            onCancel={() =>
              act(async () => {
                await client.cancelEvalRun(selectedRun.id);
                await refresh(selectedRun.id);
              })
            }
            onExport={() =>
              act(async () => {
                const exported = await client.getEvalReport(selectedRun.id);
                setExportedReport(exported);
                downloadJson(`eval-report-${selectedRun.id}.json`, exported);
              })
            }
          />
        ) : (
          <div className="empty-report">Nenhum run disponível.</div>
        )}
      </div>
    </div>
  );
}

function EvalRunView({
  run,
  report,
  busy,
  onStart,
  onCancel,
  onExport,
}: {
  run: EvalRun;
  report?: EvalReport;
  busy: boolean;
  onStart: () => void;
  onCancel: () => void;
  onExport: () => void;
}) {
  const cancelable = ["queued", "running", "canceling"].includes(run.status);
  return (
    <main className="report-panel">
      <header className="report-header">
        <div>
          <span className={`report-status report-status--${runVisualStatus(run)}`}>
            {run.status}
          </span>
          <h2>{run.experiment_id}</h2>
          <p>{run.id}</p>
        </div>
        <div className="report-actions">
          {run.status === "created" && (
            <button className="secondary-button" type="button" onClick={onStart} disabled={busy}>
              Iniciar
            </button>
          )}
          {cancelable && (
            <button className="secondary-button" type="button" onClick={onCancel} disabled={busy}>
              Cancelar
            </button>
          )}
          <button className="secondary-button" type="button" onClick={onExport} disabled={busy}>
            Exportar JSON
          </button>
        </div>
      </header>
      <dl className="run-facts">
        <div><dt>Tier</dt><dd>{run.tier}</dd></div>
        <div><dt>Phase</dt><dd>{run.phase}</dd></div>
        <div><dt>Seeds</dt><dd>{run.seeds.join(", ") || "[]"}</dd></div>
        <div><dt>Reason code</dt><dd>{run.reason_code ?? "-"}</dd></div>
        <div><dt>Created</dt><dd>{run.created_at}</dd></div>
        <div><dt>Updated</dt><dd>{run.updated_at}</dd></div>
      </dl>
      <section className="report-section raw-report">
        <div className="section-heading">
          <div>
            <span className="eyebrow">EvalReport</span>
            <h3>Payload</h3>
          </div>
          {report && <span>{report.created_at}</span>}
        </div>
        {report ? (
          <pre>{JSON.stringify(report.payload, null, 2)}</pre>
        ) : (
          <div className="interval-empty">Nenhum relatório retornado para este run.</div>
        )}
      </section>
    </main>
  );
}

function SettingsArea({
  snapshot,
  clientMode,
}: {
  snapshot: SettingsSnapshot;
  clientMode: HarnessClient["mode"];
}) {
  return (
    <div className="settings-page">
      <header className="area-header">
        <div>
          <span className="eyebrow">Control plane</span>
          <h1>Settings</h1>
          <p>Snapshot somente leitura retornado pelo servidor.</p>
        </div>
        <div className="area-header-actions">
          <MockPill mode={clientMode} />
          <button className="primary-button" type="button" disabled>
            <CircleStop size={15} /> Mutações indisponíveis
          </button>
        </div>
      </header>

      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Seções de configuração">
          <a href="#health"><Activity size={15} /> Health</a>
          <a href="#workspaces"><HardDrive size={15} /> Workspaces</a>
          <a href="#runtime"><Bot size={15} /> Runtime</a>
          <a href="#loop"><SettingsIcon size={15} /> Loop</a>
        </nav>

        <main className="settings-content">
          <section className="settings-card health-card" id="health">
            <SettingsHeading
              icon={Activity}
              title="Health"
              description="Readiness e capabilities observadas em /api/health."
            />
            <div className="health-grid">
              <HealthItem
                label="Harness"
                detail={snapshot.health.reason_code ?? "ready"}
                ready={snapshot.health.ready}
              />
              {Object.entries(snapshot.health.capabilities).map(([capability, enabled]) => (
                <HealthItem
                  key={capability}
                  label={capability}
                  detail={String(enabled)}
                  ready={enabled}
                />
              ))}
            </div>
          </section>

          <section className="settings-card" id="workspaces">
            <SettingsHeading
              icon={HardDrive}
              title="Workspaces"
              description="Raízes presentes no WorkspaceRootGrant do servidor."
              action={<button className="small-button" type="button" disabled>Adicionar indisponível</button>}
            />
            <div className="workspace-settings-list">
              {snapshot.workspaces.length ? (
                snapshot.workspaces.map((workspace) => (
                  <div key={workspace.id}>
                    <span className="workspace-icon"><HardDrive size={16} /></span>
                    <div>
                      <strong>{workspace.id}</strong>
                      <code>{workspace.root}</code>
                    </div>
                    <span className="access-pill">authorized root</span>
                  </div>
                ))
              ) : (
                <div className="settings-empty">Nenhuma raiz autorizada.</div>
              )}
            </div>
          </section>

          <section className="settings-card" id="runtime">
            <SettingsHeading
              icon={Bot}
              title="Runtime"
              description="Identidades ativas retornadas pelo snapshot."
            />
            <div className="field-grid">
              <ReadOnlyField label="RuntimeProfile" value={snapshot.runtime_profile} mono />
              <ReadOnlyField
                label="ExecutionRoute"
                value={snapshot.default_execution_route}
                mono
              />
            </div>
          </section>

          <section className="settings-card" id="loop">
            <SettingsHeading
              icon={SettingsIcon}
              title="Loop"
              description="Limites ativos, sem controles de mutação expostos."
            />
            <div className="field-grid">
              {Object.entries(snapshot.loop).map(([key, value]) => (
                <ReadOnlyField key={key} label={key} value={displayValue(value)} mono />
              ))}
            </div>
          </section>
        </main>
      </div>
    </div>
  );
}

function SettingsHeading({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: typeof Activity;
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="settings-heading">
      <span className="settings-heading-icon"><Icon size={17} /></span>
      <div>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {action}
    </div>
  );
}

function HealthItem({ label, detail, ready }: { label: string; detail: string; ready: boolean }) {
  return (
    <div className="health-item">
      <span className={`health-indicator health-indicator--${ready ? "ready" : "warning"}`} />
      <div>
        <strong>{label}</strong>
        <span>{detail}</span>
      </div>
      <span>{ready ? "ready" : "blocked"}</span>
    </div>
  );
}

function ReadOnlyField({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      <input className={mono ? "mono" : ""} value={value} readOnly disabled />
    </label>
  );
}

function reduceLiveRun(run: LiveRun, event: AgUiEvent): LiveRun {
  switch (event.type) {
    case "RUN_STARTED":
      return { ...run, status: "queued" };
    case "STEP_STARTED":
      return { ...run, status: "running", steps: run.steps + 1 };
    case "REASONING_MESSAGE_CONTENT":
      return { ...run, reasoning: run.reasoning + event.delta };
    case "TOOL_CALL_START":
      return {
        ...run,
        tools: [
          ...run.tools,
          {
            id: event.toolCallId,
            name: event.toolCallName,
            label: event.toolCallName,
            status: "running",
            arguments: {},
            summary: "",
          },
        ],
      };
    case "TOOL_CALL_ARGS":
      return {
        ...run,
        toolArguments: {
          ...run.toolArguments,
          [event.toolCallId]: (run.toolArguments[event.toolCallId] ?? "") + event.delta,
        },
      };
    case "TOOL_CALL_END":
      return {
        ...run,
        tools: run.tools.map((tool) =>
          tool.id === event.toolCallId
            ? { ...tool, arguments: parseArguments(run.toolArguments[event.toolCallId]) }
            : tool,
        ),
      };
    case "TOOL_CALL_RESULT":
      return {
        ...run,
        tools: run.tools.map((tool) =>
          tool.id === event.toolCallId ? applyLiveToolResult(tool, event.content) : tool,
        ),
      };
    case "TEXT_MESSAGE_CONTENT":
      return { ...run, content: run.content + event.delta };
    case "CUSTOM": {
      if (event.name !== "harness.turn_outcome" || !isRecord(event.value)) return run;
      const kind = event.value.outcome_kind;
      const reasonCode = event.value.reason_code;
      return typeof kind === "string" && typeof reasonCode === "string"
        ? { ...run, outcome: { kind, reasonCode } }
        : run;
    }
    case "RUN_FINISHED":
      return { ...run, status: "finished" };
    case "RUN_ERROR":
      return { ...run, status: "error", error: event.message };
    default:
      return run;
  }
}

function liveRunMessage(run: LiveRun): ChatMessage {
  return {
    id: `live-${run.runId}`,
    turnId: run.runId,
    role: "assistant",
    content: run.content || run.error || "",
    createdAt: new Date().toISOString(),
    live: true,
    ...(run.reasoning
      ? {
          reasoning: {
            summary: `Reasoning live · ${run.steps} step(s)`,
            content: run.reasoning,
            transient: true as const,
          },
        }
      : {}),
    ...(run.tools.length ? { tools: run.tools } : {}),
    ...(run.outcome ? { liveOutcome: run.outcome } : {}),
  };
}

function applyLiveToolResult(tool: ToolCall, content: string): ToolCall {
  try {
    const payload = JSON.parse(content) as Record<string, unknown>;
    const status = typeof payload.status === "string" ? payload.status : "";
    const error = payload.error;
    const data = payload.data;
    return {
      ...tool,
      status: status === "success" || status === "empty" ? "success" : "error",
      summary: status,
      ...(error !== null && error !== undefined ? { error: displayValue(error) } : {}),
      ...(data !== null && data !== undefined ? { result: displayValue(data) } : {}),
      ...(isRecord(data) && typeof data.diff === "string" ? { diff: data.diff } : {}),
    };
  } catch {
    return { ...tool, status: "error", error: content, summary: "invalid_tool_result" };
  }
}

function parseArguments(value: string | undefined): Record<string, unknown> {
  if (!value) return {};
  try {
    const parsed = JSON.parse(value) as unknown;
    return isRecord(parsed) ? parsed : { value: parsed };
  } catch {
    return { raw: value };
  }
}

function runVisualStatus(run: EvalRun) {
  if (run.status === "completed") return "passed";
  if (run.status === "failed" || run.status === "canceled") return "failed";
  if (run.status === "blocked") return "blocked";
  return "running";
}

function formatTimestamp(value: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function displayValue(value: unknown) {
  if (typeof value === "string") return value;
  if (value === null) return "null";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Falha inesperada no client live.";
}

function makeId() {
  return globalThis.crypto?.randomUUID?.() ?? `run-${Date.now()}-${Math.random()}`;
}

function isRecord(value: unknown): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function downloadJson(fileName: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  anchor.click();
  URL.revokeObjectURL(url);
}
