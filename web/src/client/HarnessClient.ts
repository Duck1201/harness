import type {
  AgUiEvent,
  AgUiTerminalEvent,
  ApiPendingRequest,
  ChatSnapshot,
  Conversation,
  EvalPhase,
  EvalReport,
  EvalRun,
  EvalTier,
  EvalsSnapshot,
  Experiment,
  FeedbackRecord,
  Grant,
  HealthSnapshot,
  PendingConfirmation,
  RegressionDraft,
  RunAgentInput,
  SessionStatus,
  SettingsSnapshot,
  SetupStatus,
  SetupSubmission,
  Workspace,
} from "../types";

export interface HarnessClient {
  readonly mode: "mock" | "live";

  getHealth(): Promise<HealthSnapshot>;
  listWorkspaces(): Promise<Workspace[]>;
  listConversations(includeArchived?: boolean): Promise<Conversation[]>;
  getConversation(conversationId: string): Promise<Conversation>;
  createConversation(workspaceRoot: string, name?: string): Promise<Conversation>;
  selectConversation(conversationId: string): Promise<ChatSnapshot>;
  renameConversation(conversationId: string, name: string): Promise<Conversation>;
  archiveConversation(conversationId: string, archived?: boolean): Promise<Conversation>;
  deleteConversation(conversationId: string): Promise<void>;

  getChatSnapshot(conversationId?: string | null): Promise<ChatSnapshot>;
  enqueueRequest(conversationId: string, content: string): Promise<ApiPendingRequest>;
  streamAgent(
    input: RunAgentInput,
    onEvent: (event: AgUiEvent) => void,
    signal?: AbortSignal,
  ): Promise<AgUiTerminalEvent>;
  editPendingRequest(
    conversationId: string,
    requestId: string,
    content: string,
  ): Promise<ApiPendingRequest>;
  cancelPendingRequest(
    conversationId: string,
    requestId: string,
  ): Promise<ApiPendingRequest>;
  listGrants(conversationId: string): Promise<Grant[]>;
  addGrant(conversationId: string, permission: string): Promise<Grant>;
  revokeGrant(conversationId: string, grantId: string): Promise<void>;
  getPendingConfirmation(
    conversationId: string,
  ): Promise<PendingConfirmation | null>;
  resolveConfirmation(
    conversationId: string,
    confirmationId: string,
    approved: boolean,
  ): Promise<void>;
  stop(conversationId: string): Promise<void>;
  addFeedback(
    conversationId: string,
    rating: number,
    turnId?: string,
    comment?: string,
  ): Promise<FeedbackRecord>;

  getEvalsSnapshot(): Promise<EvalsSnapshot>;
  listEvalExperiments(): Promise<Experiment[]>;
  listEvalRuns(): Promise<EvalRun[]>;
  getEvalRun(runId: string): Promise<Record<string, unknown>>;
  createEvalRun(input: {
    experimentId: string;
    tier: EvalTier;
    phase: EvalPhase;
    seeds?: number[];
  }): Promise<EvalRun>;
  startEvalRun(runId: string): Promise<EvalRun>;
  cancelEvalRun(runId: string): Promise<EvalRun>;
  listEvalReports(): Promise<EvalReport[]>;
  getEvalReport(runId: string): Promise<EvalReport>;
  listRegressionDrafts(): Promise<RegressionDraft[]>;
  createRegressionDraft(
    conversationId: string,
    feedbackId: string,
  ): Promise<RegressionDraft>;

  getSettingsSnapshot(): Promise<SettingsSnapshot>;

  getSessionStatus(): Promise<SessionStatus>;
  login(password: string): Promise<SessionStatus>;
  logout(): Promise<void>;

  getSetupStatus(): Promise<SetupStatus>;
  completeSetup(token: string, submission: SetupSubmission): Promise<void>;
}
