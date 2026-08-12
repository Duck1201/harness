# pyright: reportUnusedFunction=false

import sys
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .ag_ui import encode_sse, project_agent_event, run_error_event, run_started_event
from .application_service import ApplicationService, ApplicationServiceError
from .auth import AuthenticationError, SessionController, hash_password, verify_password
from .config import load_config
from .conversation_store import ConversationStore, NotFoundError
from .domain import (
    CanonicalHistoryEntry,
    Conversation,
    Feedback,
    Grant,
    PendingRequest,
    Turn,
)
from .evals import (
    EvalArm,
    EvalCase,
    EvalNotFoundError,
    EvalPhase,
    EvalReport,
    EvalRun,
    EvalServiceError,
    EvalTier,
    RegressionDraft,
)
from .host_config import (
    CredentialStore,
    HostConfig,
    HostConfigStore,
    default_state_dir,
)
from .observability_store import ObservabilityStore
from .ollama_runtime import OllamaRuntime
from .ports import ConfirmationRequest
from .setup import SetupController, SetupError, SetupSubmission, validated_host_config
from .system_prompt import load_operator_notes
from .token_estimator import HuggingFaceTokenEstimator

DEFAULT_PORT = 8765
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
# O contrato não é ajustável pelo host: o caminho é fixo por construção.
HARNESS_CONFIG_PATH = Path("config/harness.json")


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateConversationRequest(ApiModel):
    workspace_root: str
    name: str = "New conversation"


class UpdateConversationRequest(ApiModel):
    name: str | None = None
    archived: bool | None = None
    yolo_disabled: bool | None = None


class YoloRequest(ApiModel):
    enabled: bool


class RequestContent(ApiModel):
    content: str


class GrantRequest(ApiModel):
    permission: str


class ConfirmationDecisionRequest(ApiModel):
    approved: bool
    waive: bool = False


class LoginRequest(ApiModel):
    password: str


class OperatorPasswordRequest(ApiModel):
    password: str
    current_password: str | None = None


class FeedbackRequest(ApiModel):
    rating: int = Field(ge=-1, le=1)
    comment: str | None = None
    turn_id: str | None = None


class _AgUiMessage(ApiModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)


class AgUiDeveloperMessage(_AgUiMessage):
    role: Literal["developer"]
    content: str


class AgUiSystemMessage(_AgUiMessage):
    role: Literal["system"]
    content: str


class AgUiAssistantMessage(_AgUiMessage):
    role: Literal["assistant"]
    content: str | None = None


class AgUiUserMessage(_AgUiMessage):
    role: Literal["user"]
    content: str


class AgUiToolMessage(_AgUiMessage):
    role: Literal["tool"]
    content: str
    toolCallId: str = Field(min_length=1)


class RunAgentInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    threadId: str = Field(min_length=1)
    runId: str | None = Field(default=None, min_length=1)
    messages: list[
        Annotated[
            AgUiDeveloperMessage
            | AgUiSystemMessage
            | AgUiAssistantMessage
            | AgUiUserMessage
            | AgUiToolMessage,
            Field(discriminator="role"),
        ]
    ]
    state: Any = None
    context: Any = None
    tools: Any = None


class CreateEvalRunRequest(ApiModel):
    experiment_id: str
    tier: EvalTier
    phase: EvalPhase
    seeds: tuple[int, ...] | None = None


class RegressionDraftRequest(ApiModel):
    conversation_id: str
    feedback_id: str


def create_app(
    *,
    service: ApplicationService | None = None,
    allowed_origins: Sequence[str] | None = None,
    setup_controller: SetupController | None = None,
    host_config_path: str | Path | None = None,
    setup_token: str | None = None,
    setup_ttl_seconds: float = 600,
    reopen_setup: bool = False,
    port: int = DEFAULT_PORT,
    static_dir: str | Path = Path("web/dist"),
    max_body_bytes: int = 1024 * 1024,
    session_controller: SessionController | None = None,
    credential_store: CredentialStore | None = None,
) -> FastAPI:
    if max_body_bytes < 1:
        raise ValueError("max_body_bytes must be positive")
    effective_origins = tuple(allowed_origins or ())
    operator_password_hash: str | None = None
    credentials: CredentialStore | None = credential_store
    if service is None:
        host_store = _host_config_store(host_config_path)
        host_config = host_store.load_optional()
        if allowed_origins is None:
            effective_origins = configured_origins(host_config, port)
        host_credentials = credential_store or CredentialStore(host_store.credentials_path)
        credentials = host_credentials
        operator_password_hash = host_credentials.read_operator_password_hash()
        service = _default_service(
            host_store=host_store,
            credential_store=host_credentials,
        )
        if setup_controller is None:
            setup_controller = SetupController(
                host_store,
                credential_store=host_credentials,
                reopen=reopen_setup,
                token=setup_token,
                ttl_seconds=setup_ttl_seconds,
            )
            message = setup_controller.boot_console_message()
            if message is not None:
                print(message, file=sys.stderr)
    origins = frozenset(effective_origins)
    application_service = service
    host_store = _host_config_store(host_config_path) if credentials is not None else None
    sessions = session_controller or SessionController(password_hash=operator_password_hash)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        del app
        await application_service.initialize()
        try:
            yield
        finally:
            await application_service.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(effective_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "X-Harness-Setup-Token"],
    )
    app.add_middleware(_OperatorAuthenticationMiddleware, sessions=sessions)
    app.add_middleware(_BodyLimitMiddleware, max_body_bytes=max_body_bytes)
    app.add_middleware(_OriginAllowlistMiddleware, allowed_origins=origins)

    @app.exception_handler(ApplicationServiceError)
    async def application_error(request: Request, error: ApplicationServiceError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, error: AuthenticationError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.exception_handler(NotFoundError)
    async def not_found(request: Request, error: NotFoundError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": str(error)}},
        )

    @app.exception_handler(EvalNotFoundError)
    async def eval_not_found(request: Request, error: EvalNotFoundError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "eval_not_found", "message": str(error)}},
        )

    @app.exception_handler(EvalServiceError)
    async def eval_service_error(request: Request, error: EvalServiceError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "invalid_eval_request", "message": str(error)}},
        )

    @app.exception_handler(SetupError)
    async def setup_error(request: Request, error: SetupError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=error.status_code,
            content={"error": {"code": error.code, "message": str(error)}},
        )

    @app.get("/api/setup/status")
    async def setup_status() -> dict[str, Any]:
        if setup_controller is None:
            state_dir = default_state_dir()
            return {
                "configured": True,
                "required": False,
                "restart_required": False,
                "token_expires_at": None,
                "suggested_state_dir": str(state_dir),
                "suggested_tokenizer_path": str(state_dir / "tokenizer.json"),
            }
        return setup_controller.status.model_dump(mode="json")

    @app.post("/api/setup")
    async def setup(request: Request) -> Response:
        if setup_controller is None:
            raise SetupError(
                "setup_not_available",
                "Setup is not available for this boot.",
                status_code=409,
            )
        if not _is_direct_loopback(request):
            raise SetupError(
                "direct_loopback_required",
                "Setup requires a direct loopback connection.",
                status_code=403,
            )
        origin = request.headers.get("origin")
        if origin is None or origin not in origins:
            raise SetupError(
                "origin_not_allowed",
                "The request Origin is not allowed.",
                status_code=403,
            )
        supplied_token = request.headers.get("x-harness-setup-token")
        setup_controller.authorize(supplied_token)
        try:
            payload = SetupSubmission.model_validate(await request.json())
        except Exception:
            return JSONResponse(
                status_code=422,
                content={
                    "error": {
                        "code": "invalid_setup_request",
                        "message": "The setup request is invalid.",
                    }
                },
            )
        setup_controller.complete(supplied_token, payload)
        return JSONResponse(content={"restart_required": True})

    @app.get("/api/session")
    async def session_status(request: Request) -> dict[str, Any]:
        status = sessions.status(request.headers.get("x-harness-session"))
        return {
            "authentication_required": status.authentication_required,
            "authenticated": status.authenticated,
            "expires_at": (
                status.expires_at.isoformat() if status.expires_at is not None else None
            ),
        }

    @app.post("/api/session", status_code=201)
    async def login(payload: LoginRequest) -> dict[str, Any]:
        token, expires_at = sessions.login(payload.password)
        return {"session": token, "expires_at": expires_at.isoformat()}

    @app.delete("/api/session", status_code=204)
    async def logout(request: Request) -> Response:
        sessions.logout(request.headers.get("x-harness-session"))
        return Response(status_code=204)

    @app.put("/api/admin/operator-password", status_code=204)
    async def set_operator_password(payload: OperatorPasswordRequest) -> Response:
        # Reachable without a session only while none is configured, and only from
        # loopback: the authentication middleware enforces both.
        if credentials is None:
            raise AuthenticationError(
                "credential_store_unavailable",
                "This app was built without a credential store.",
                status_code=409,
            )
        # A session alone must not rotate the credential: a stolen token would turn
        # into permanent access and lock the Operator out of their own host.
        current_hash = credentials.read_operator_password_hash()
        if current_hash is not None and not verify_password(
            payload.current_password or "", current_hash
        ):
            raise AuthenticationError(
                "invalid_credentials",
                "The current Operator password is incorrect.",
                status_code=401,
            )
        try:
            digest = hash_password(payload.password)
        except ValueError as error:
            raise SetupError("weak_operator_password", str(error), status_code=422) from error
        credentials.write_operator_password_hash(digest)
        sessions.set_password_hash(digest)
        return Response(status_code=204)

    @app.put("/api/admin/host-config")
    async def update_host_config(payload: SetupSubmission) -> dict[str, Any]:
        # Mesma validação do setup, mesma escrita atômica: o painel não é um
        # caminho alternativo, é o mesmo caminho com outra porta de entrada.
        if host_store is None:
            raise SetupError(
                "host_config_unavailable",
                "This app was built without a host configuration store.",
                status_code=409,
            )
        host_store.write(validated_host_config(payload))
        # Nada é reconstruído a quente: trocar roots, tokenizer ou origin no meio
        # de um Turn mexeria em policy e executor já construídos.
        return {"restart_required": True}

    @app.put("/api/admin/yolo", status_code=204)
    async def set_yolo(payload: YoloRequest) -> Response:
        # Standing Operator decision, host-wide: while it is on, the gate answers
        # yes to every confirmation, including a write derived from web content.
        await application_service.set_yolo_enabled(payload.enabled)
        return Response(status_code=204)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        readiness = application_service.readiness
        return {
            "ready": readiness.ready,
            "reason_code": readiness.reason_code,
            "capabilities": {
                "settings_mutation": host_store is not None,
                "admin": credentials is not None,
                "authentication_required": sessions.authentication_required,
                "ag_ui_sse": True,
                "eval_runner": True,
                "static_spa": Path(static_dir).is_dir(),
            },
        }

    @app.get("/api/workspaces")
    async def workspaces() -> dict[str, Any]:
        return {
            "workspaces": [
                {"id": workspace.id, "root": str(workspace.root)}
                for workspace in application_service.list_workspaces()
            ]
        }

    @app.post("/api/conversations", status_code=201)
    async def create_conversation(payload: CreateConversationRequest) -> dict[str, Any]:
        conversation = await application_service.create_conversation(
            payload.workspace_root, name=payload.name
        )
        return {"conversation": _conversation_json(conversation)}

    @app.get("/api/conversations")
    async def list_conversations(include_archived: bool = False) -> dict[str, Any]:
        conversations = await application_service.list_conversations(
            include_archived=include_archived
        )
        return {"conversations": [_conversation_json(item) for item in conversations]}

    @app.get("/api/conversations/{conversation_id}")
    async def get_conversation(conversation_id: str) -> dict[str, Any]:
        conversation = await application_service.get_conversation(conversation_id)
        return {"conversation": _conversation_json(conversation)}

    @app.patch("/api/conversations/{conversation_id}")
    async def update_conversation(
        conversation_id: str, payload: UpdateConversationRequest
    ) -> dict[str, Any]:
        conversation = await application_service.update_conversation(
            conversation_id,
            name=payload.name,
            archived=payload.archived,
            yolo_disabled=payload.yolo_disabled,
        )
        return {"conversation": _conversation_json(conversation)}

    @app.delete("/api/conversations/{conversation_id}", status_code=204)
    async def delete_conversation(conversation_id: str) -> Response:
        await application_service.delete_conversation(conversation_id)
        return Response(status_code=204)

    @app.post("/api/conversations/{conversation_id}/requests", status_code=202)
    async def enqueue_request(conversation_id: str, payload: RequestContent) -> dict[str, Any]:
        pending = await application_service.enqueue_request(conversation_id, payload.content)
        return {"request": _pending_request_json(pending)}

    @app.patch("/api/conversations/{conversation_id}/requests/{request_id}")
    async def edit_request(
        conversation_id: str,
        request_id: str,
        payload: RequestContent,
    ) -> dict[str, Any]:
        pending = await application_service.edit_pending_request(
            conversation_id, request_id, payload.content
        )
        return {"request": _pending_request_json(pending)}

    @app.delete("/api/conversations/{conversation_id}/requests/{request_id}")
    async def cancel_request(conversation_id: str, request_id: str) -> dict[str, Any]:
        pending = await application_service.cancel_pending_request(conversation_id, request_id)
        return {"request": _pending_request_json(pending)}

    @app.get("/api/conversations/{conversation_id}/grants")
    async def list_grants(conversation_id: str) -> dict[str, Any]:
        grants = await application_service.list_grants(conversation_id)
        return {"grants": [_grant_json(grant) for grant in grants]}

    @app.post("/api/conversations/{conversation_id}/grants", status_code=201)
    async def grant(conversation_id: str, payload: GrantRequest) -> dict[str, Any]:
        created = await application_service.grant(conversation_id, payload.permission)
        return {"grant": _grant_json(created)}

    @app.delete("/api/conversations/{conversation_id}/grants/{grant_id}", status_code=204)
    async def revoke_grant(conversation_id: str, grant_id: str) -> Response:
        await application_service.revoke_grant(conversation_id, grant_id)
        return Response(status_code=204)

    @app.get("/api/conversations/{conversation_id}/confirmation")
    async def get_confirmation(conversation_id: str) -> dict[str, Any]:
        pending = await application_service.pending_confirmation(conversation_id)
        return {"confirmation": _confirmation_json(pending)}

    @app.post("/api/conversations/{conversation_id}/confirmation/{confirmation_id}")
    async def resolve_confirmation(
        conversation_id: str,
        confirmation_id: str,
        payload: ConfirmationDecisionRequest,
    ) -> dict[str, bool]:
        await application_service.resolve_confirmation(
            conversation_id,
            confirmation_id,
            approved=payload.approved,
            waive=payload.waive,
        )
        return {"approved": payload.approved}

    @app.delete(
        "/api/conversations/{conversation_id}/confirmation-waivers/{effect}",
        status_code=204,
    )
    async def revoke_confirmation_waiver(conversation_id: str, effect: str) -> Response:
        await application_service.revoke_confirmation_waiver(conversation_id, effect)
        return Response(status_code=204)

    @app.post("/api/conversations/{conversation_id}/stop", status_code=202)
    async def stop(conversation_id: str) -> dict[str, bool]:
        await application_service.stop(conversation_id)
        return {"stop_requested": True}

    @app.post("/api/conversations/{conversation_id}/feedback", status_code=201)
    async def feedback(conversation_id: str, payload: FeedbackRequest) -> dict[str, Any]:
        recorded = await application_service.add_feedback(
            conversation_id,
            rating=payload.rating,
            comment=payload.comment,
            turn_id=payload.turn_id,
        )
        return {"feedback": _feedback_json(recorded)}

    @app.get("/api/evals/experiments")
    async def list_eval_experiments() -> dict[str, Any]:
        manifest = application_service.eval_service.catalog.manifest
        return {
            "experiments": [
                experiment.model_dump(mode="json")
                for experiment in application_service.eval_service.list_experiments()
            ],
            "promotion_protocol": manifest.promotion_protocol.model_dump(mode="json"),
        }

    @app.get("/api/evals/runs")
    async def list_eval_runs() -> dict[str, Any]:
        runs = await application_service.eval_service.list_runs()
        return {"runs": [_eval_run_json(run) for run in runs]}

    @app.post("/api/evals/runs", status_code=201)
    async def create_eval_run(payload: CreateEvalRunRequest) -> dict[str, Any]:
        run = await application_service.eval_service.create_run(
            experiment_id=payload.experiment_id,
            tier=payload.tier,
            phase=payload.phase,
            seeds=payload.seeds,
        )
        return {"run": _eval_run_json(run)}

    @app.get("/api/evals/runs/{run_id}")
    async def get_eval_run(run_id: str) -> dict[str, Any]:
        return await _eval_run_detail(application_service, run_id)

    @app.post("/api/evals/runs/{run_id}", status_code=202)
    @app.post("/api/evals/runs/{run_id}/start", status_code=202)
    async def start_eval_run(run_id: str) -> dict[str, Any]:
        run = await application_service.eval_service.start(run_id)
        return {"run": _eval_run_json(run)}

    @app.post("/api/evals/runs/{run_id}/cancel", status_code=202)
    async def cancel_eval_run(run_id: str) -> dict[str, Any]:
        run = await application_service.eval_service.cancel(run_id)
        return {"run": _eval_run_json(run)}

    @app.get("/api/evals/reports")
    async def list_eval_reports() -> dict[str, Any]:
        reports = await application_service.eval_service.list_reports()
        return {"reports": [_eval_report_json(report) for report in reports]}

    @app.get("/api/evals/reports/{run_id}")
    async def export_eval_report(run_id: str) -> dict[str, Any]:
        report = await application_service.eval_service.report(run_id)
        return {"report": _eval_report_json(report)}

    @app.post("/api/evals/regression-drafts", status_code=201)
    async def create_regression_draft(
        payload: RegressionDraftRequest,
    ) -> dict[str, Any]:
        draft = await application_service.create_regression_draft(
            payload.conversation_id, payload.feedback_id
        )
        return {"draft": _regression_draft_json(draft)}

    @app.get("/api/evals/regression-drafts")
    async def list_regression_drafts() -> dict[str, Any]:
        drafts = await application_service.eval_service.list_regression_drafts()
        return {"drafts": [_regression_draft_json(draft) for draft in drafts]}

    @app.get("/api/ui/chat")
    async def chat_snapshot(conversation_id: str) -> dict[str, Any]:
        snapshot = await application_service.chat_snapshot(conversation_id)
        conversation = cast(Conversation, snapshot["conversation"])
        pending = cast(list[PendingRequest], snapshot["pending_requests"])
        history = cast(list[CanonicalHistoryEntry], snapshot["history"])
        active = cast(Turn | None, snapshot["active_turn"])
        turns = cast(list[Turn], snapshot["turns"])
        feedback_items = cast(list[Feedback], snapshot["feedback"])
        confirmation = cast(ConfirmationRequest | None, snapshot["pending_confirmation"])
        return {
            "conversation": _conversation_json(conversation),
            "pending_requests": [_pending_request_json(item) for item in pending],
            "history": [_history_json(item) for item in history],
            "active_turn": _turn_json(active) if active is not None else None,
            "turns": [_turn_json(item) for item in turns],
            "feedback": [_feedback_json(item) for item in feedback_items],
            "pending_confirmation": _confirmation_json(confirmation),
            "confirmation_waivers": cast(list[str], snapshot["confirmation_waivers"]),
            "yolo": cast(bool, snapshot["yolo"]),
        }

    @app.get("/api/ui/evals")
    async def evals_snapshot() -> dict[str, Any]:
        runs = await application_service.eval_service.list_runs()
        reports = await application_service.eval_service.list_reports()
        return {
            "experiments": [
                experiment.model_dump(mode="json")
                for experiment in application_service.eval_service.list_experiments()
            ],
            "runs": [_eval_run_json(run) for run in runs],
            "reports": [_eval_report_json(report) for report in reports],
            "capabilities": {
                "eval_runner": True,
                "progress_transport": "polling_json",
            },
        }

    @app.get("/api/ui/settings")
    async def settings_snapshot() -> dict[str, Any]:
        status = setup_controller.status if setup_controller is not None else None
        stored = host_store.load_optional() if host_store is not None else None
        return {
            "mutable": host_store is not None,
            "host_config": stored.model_dump(mode="json") if stored is not None else None,
            "setup_required": status.required if status is not None else False,
            "restart_required": (status.restart_required if status is not None else False),
            "default_execution_route": application_service.config.default_execution_route,
            "runtime_profile": application_service.config.runtime_profile.id,
            "yolo_enabled": await application_service.yolo_enabled(),
            "loop": application_service.config.loop.model_dump(mode="json"),
        }

    @app.post("/api/agent")
    async def agent(payload: RunAgentInput) -> StreamingResponse:
        latest_user_message = next(
            (
                message
                for message in reversed(payload.messages)
                if isinstance(message, AgUiUserMessage)
            ),
            None,
        )
        if latest_user_message is None:
            raise ApplicationServiceError(
                "ag_ui_user_message_required",
                "RunAgentInput.messages must contain a user message.",
                status_code=422,
            )

        thread_id = payload.threadId
        run_id = payload.runId or str(uuid4())
        await application_service.get_conversation(thread_id)

        async def stream() -> AsyncIterator[str]:
            queue = application_service.subscribe(thread_id)
            terminal_sent = False
            try:
                pending = await application_service.enqueue_request(
                    thread_id, latest_user_message.content
                )
                yield encode_sse(
                    run_started_event(
                        conversation_id=thread_id,
                        run_id=run_id,
                    )
                )
                while True:
                    event = await queue.get()
                    if event.request_id != pending.id:
                        continue
                    for projected in project_agent_event(event, run_id=run_id):
                        if projected["type"] in {"RUN_FINISHED", "RUN_ERROR"}:
                            terminal_sent = True
                        yield encode_sse(projected)
                    if event.kind.value == "turn_finished":
                        if not terminal_sent:
                            terminal_sent = True
                            yield encode_sse(run_error_event("missing_terminal_event"))
                        return
            except Exception as error:
                if not terminal_sent:
                    yield encode_sse(run_error_event(type(error).__name__))
            finally:
                application_service.unsubscribe(thread_id, queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    static_root = Path(static_dir)
    if static_root.is_dir() and (static_root / "index.html").is_file():
        resolved_static_root = static_root.resolve()
        index_path = resolved_static_root / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str) -> Response:
            if full_path == "api" or full_path.startswith("api/"):
                return JSONResponse(
                    status_code=404,
                    content={
                        "error": {
                            "code": "not_found",
                            "message": "API endpoint not found.",
                        }
                    },
                )
            try:
                candidate = (resolved_static_root / full_path).resolve()
            except OSError:
                candidate = index_path
            if candidate.is_relative_to(resolved_static_root) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_path)

    return app


def _default_service(
    *,
    host_store: HostConfigStore | None = None,
    credential_store: CredentialStore | None = None,
) -> ApplicationService:
    """Builds the service from host.json alone.

    There is no environment override: the file is the single source, so what the
    Settings tab writes is what the next boot reads. Only what has to exist before
    the app does — bind host, port, which file to read — comes from CLI flags.
    """
    selected_host_store = host_store or _host_config_store()
    host_config = selected_host_store.load_optional()
    del credential_store  # a senha do Operator é o único segredo, e ela tem rota própria
    config = load_config(HARNESS_CONFIG_PATH)
    state_dir = host_config.state_dir if host_config is not None else default_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    roots = host_config.allowed_workspace_roots if host_config is not None else ()
    tokenizer_path = (
        host_config.tokenizer_path if host_config is not None else state_dir / "tokenizer.json"
    )
    tokenizer_digest = host_config.tokenizer_digest if host_config is not None else ""
    runtime = OllamaRuntime(
        base_url=(host_config.ollama_url if host_config is not None else DEFAULT_OLLAMA_URL),
        model=config.runtime_profile.model.id,
        expected_digest=config.runtime_profile.profile_digest_sha256,
    )
    estimator = HuggingFaceTokenEstimator(
        tokenizer_path,
        expected_sha256=tokenizer_digest,
    )
    return ApplicationService(
        store=ConversationStore(state_dir / "conversations.sqlite3"),
        observability_store=ObservabilityStore(state_dir / "observability.sqlite3"),
        config=config,
        runtime=runtime,
        estimator=estimator,
        allowed_workspace_roots=roots,
        search_endpoint=host_config.searxng_url if host_config is not None else None,
        browser_executable=host_config.browser_executable if host_config is not None else None,
        operator_notes=load_operator_notes(),
    )


def _is_direct_loopback(request: Request) -> bool:
    client = request.client
    if client is None:
        return False
    try:
        if not ip_address(client.host).is_loopback:
            return False
    except ValueError:
        return False
    return not any(
        header in {"forwarded", "via", "x-real-ip"} or header.startswith("x-forwarded-")
        for header in request.headers
    )


def _host_config_store(path: str | Path | None = None) -> HostConfigStore:
    return HostConfigStore(path)


def configured_origins(host_config: HostConfig | None, port: int = DEFAULT_PORT) -> tuple[str, ...]:
    if host_config is not None:
        return host_config.allowed_origins
    # Before setup writes a HostConfig, the only origin that can reach the API is
    # the one the SPA is served from — this server's own port. Hardcoding a port
    # here leaves a fresh install unable to complete its own setup form.
    return tuple(f"http://{host}:{port}" for host in ("127.0.0.1", "localhost"))


def _conversation_json(conversation: Conversation) -> dict[str, Any]:
    payload = asdict(conversation)
    payload["created_at"] = conversation.created_at.isoformat()
    payload["updated_at"] = conversation.updated_at.isoformat()
    payload["last_active_at"] = conversation.last_active_at.isoformat()
    payload["archived_at"] = (
        conversation.archived_at.isoformat() if conversation.archived_at is not None else None
    )
    return payload


def _pending_request_json(pending: PendingRequest) -> dict[str, Any]:
    return {
        "id": pending.id,
        "conversation_id": pending.conversation_id,
        "sequence": pending.sequence,
        "content": pending.content,
        "status": pending.status.value,
        "created_at": pending.created_at.isoformat(),
        "updated_at": pending.updated_at.isoformat(),
    }


def _history_json(entry: CanonicalHistoryEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "sequence": entry.sequence,
        "conversation_id": entry.conversation_id,
        "turn_id": entry.turn_id,
        "kind": entry.kind.value,
        "payload": entry.payload,
        "created_at": entry.created_at.isoformat(),
    }


def _turn_json(turn: Turn) -> dict[str, Any]:
    outcome = turn.terminal_outcome
    return {
        "id": turn.id,
        "conversation_id": turn.conversation_id,
        "request_id": turn.request_id,
        "status": turn.status.value,
        "started_at": turn.started_at.isoformat(),
        "ended_at": turn.ended_at.isoformat() if turn.ended_at is not None else None,
        "terminal_outcome": (
            {
                "kind": outcome.kind.value,
                "reason_code": outcome.reason_code,
                "recorded_at": outcome.recorded_at.isoformat(),
                "detail": outcome.detail,
            }
            if outcome is not None
            else None
        ),
    }


def _confirmation_json(request: ConfirmationRequest | None) -> dict[str, Any] | None:
    if request is None:
        return None
    return {
        "id": request.id,
        "conversation_id": request.conversation_id,
        "turn_id": request.turn_id,
        "step_sequence": request.step_sequence,
        "reason_code": request.reason_code,
        "tool_calls": [
            {"id": call.id, "name": call.name, "arguments": dict(call.arguments)}
            for call in request.tool_calls
        ],
        "previews": [
            {
                "tool_call_id": preview.tool_call_id,
                "path": preview.path,
                "kind": preview.kind,
                "diff": preview.diff,
                "truncated": preview.truncated,
            }
            for preview in request.previews
        ],
    }


def _grant_json(grant: Grant) -> dict[str, Any]:
    return {
        "id": grant.id,
        "conversation_id": grant.conversation_id,
        "permission": grant.permission,
        "scope": grant.scope,
        "granted_at": grant.granted_at.isoformat(),
        "expires_at": grant.expires_at.isoformat() if grant.expires_at is not None else None,
    }


def _feedback_json(feedback: Feedback) -> dict[str, Any]:
    return {
        "id": feedback.id,
        "conversation_id": feedback.conversation_id,
        "turn_id": feedback.turn_id,
        "rating": feedback.rating,
        "comment": feedback.comment,
        "created_at": feedback.created_at.isoformat(),
    }


async def _eval_run_detail(service: ApplicationService, run_id: str) -> dict[str, Any]:
    run = await service.eval_service.status(run_id)
    arms = await service.eval_service.store.list_arms(run_id)
    cases = await service.eval_service.store.list_cases(run_id)
    metrics = await service.eval_service.store.list_metrics(run_id)
    planned_per_arm = 15 if run.phase is EvalPhase.PILOT else 50
    return {
        "run": _eval_run_json(run),
        "arms": [_eval_arm_json(arm) for arm in arms],
        "cases": [_eval_case_json(case) for case in cases],
        "metrics": [
            {
                "id": metric.id,
                "run_id": metric.run_id,
                "arm_id": metric.arm_id,
                "case_id": metric.case_id,
                "name": metric.name,
                "value": metric.value,
                "created_at": metric.created_at.isoformat(),
            }
            for metric in metrics
        ],
        "progress": {
            "completed_cases": len(cases),
            "planned_cases": planned_per_arm * len(arms),
        },
    }


def _eval_run_json(run: EvalRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "experiment_id": run.experiment_id,
        "tier": run.tier.value,
        "phase": run.phase.value,
        "status": run.status.value,
        "seeds": list(run.seeds),
        "reason_code": run.reason_code,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }


def _eval_arm_json(arm: EvalArm) -> dict[str, Any]:
    return {
        "id": arm.id,
        "run_id": arm.run_id,
        "arm_id": arm.arm_id,
        "settings": arm.settings,
        "created_at": arm.created_at.isoformat(),
    }


def _eval_case_json(case: EvalCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "run_id": case.run_id,
        "arm_id": case.arm_id,
        "fixture_id": case.fixture_id,
        "seed": case.seed,
        "order_index": case.order_index,
        "verdict": case.verdict.value,
        "terminal_outcome_kind": (
            case.terminal_outcome_kind.value if case.terminal_outcome_kind is not None else None
        ),
        "terminal_outcome_reason": case.terminal_outcome_reason,
        "security_violations": case.security_violations,
        "created_at": case.created_at.isoformat(),
    }


def _eval_report_json(report: EvalReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "run_id": report.run_id,
        "payload": report.payload,
        "created_at": report.created_at.isoformat(),
    }


def _regression_draft_json(draft: RegressionDraft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "source_feedback_sha256": draft.source_feedback_sha256,
        "source_turn_sha256": draft.source_turn_sha256,
        "rating": draft.rating,
        "comment_present": draft.comment_present,
        "terminal_outcome_kind": draft.terminal_outcome_kind.value,
        "terminal_outcome_reason": draft.terminal_outcome_reason,
        "created_at": draft.created_at.isoformat(),
    }


class _OriginAllowlistMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_origins: frozenset[str]) -> None:
        self._app = app
        self._allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            raw_origin = headers.get(b"origin")
            if raw_origin is not None:
                try:
                    origin = raw_origin.decode("ascii")
                except UnicodeDecodeError:
                    origin = ""
                if origin not in self._allowed_origins:
                    response = JSONResponse(
                        status_code=403,
                        content={
                            "error": {
                                "code": "origin_not_allowed",
                                "message": "The request Origin is not allowed.",
                            }
                        },
                    )
                    await response(scope, receive, send)
                    return
        await self._app(scope, receive, send)


class _OperatorAuthenticationMiddleware:
    """Fail-closed network exposure (ADR-0005).

    With no Operator password configured the server answers only direct loopback
    connections; with one configured every API route needs a session. The
    exceptions are the routes that must work before a login exists.
    """

    _OPEN_PATHS = frozenset({"/api/health", "/api/session", "/api/setup", "/api/setup/status"})

    def __init__(self, app: ASGIApp, *, sessions: SessionController) -> None:
        self._app = app
        self._sessions = sessions

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        if path.startswith("/api/") and path not in self._OPEN_PATHS:
            request = Request(scope)
            try:
                if self._sessions.authentication_required:
                    self._sessions.authorize(request.headers.get("x-harness-session"))
                elif not _is_direct_loopback(request):
                    raise AuthenticationError(
                        "authentication_required",
                        "This host has no Operator password, so only loopback is served.",
                        status_code=401,
                    )
            except AuthenticationError as error:
                response = JSONResponse(
                    status_code=error.status_code,
                    content={"error": {"code": error.code, "message": str(error)}},
                )
                await response(scope, receive, send)
                return
        await self._app(scope, receive, send)


class _BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self._app = app
        self._max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        messages: list[Message] = []
        total = 0
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            if total > self._max_body_bytes:
                response = JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "request_body_too_large",
                            "message": "The request body exceeds the server limit.",
                        }
                    },
                )
                await response(scope, receive, send)
                return
            if not message.get("more_body", False):
                break

        index = 0

        async def replay() -> Message:
            nonlocal index
            if index >= len(messages):
                return await receive()
            message = messages[index]
            index += 1
            return message

        await self._app(scope, replay, send)
