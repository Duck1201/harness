from collections.abc import Sequence
from pathlib import Path
from time import monotonic, sleep
from typing import cast

from fastapi.testclient import TestClient

from harness import (
    ApplicationService,
    ConversationStore,
    EngineReadiness,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ObservabilityStore,
    ToolSchema,
    create_app,
    load_config,
)


class FakeEstimator:
    validated = True
    readiness = EngineReadiness(ready=True)

    def estimate(self, messages: Sequence[ModelMessage], tools: Sequence[ToolSchema]) -> int:
        return len(messages) + len(tools)


class FakeRuntime:
    async def verify_profile(self) -> EngineReadiness:
        return EngineReadiness(ready=True)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        return ModelResponse(content="ok")

    async def aclose(self) -> None:
        return None


def _app(tmp_path: Path, workspace: Path):  # pyright: ignore[reportUnknownParameterType, reportMissingReturnType]
    service = ApplicationService(
        store=ConversationStore(tmp_path / "conversations.sqlite3"),
        observability_store=ObservabilityStore(tmp_path / "observability.sqlite3"),
        config=load_config(),
        runtime=FakeRuntime(),
        estimator=FakeEstimator(),
        allowed_workspace_roots=(workspace,),
    )
    return create_app(service=service, static_dir=tmp_path / "missing-dist")


def test_eval_api_runs_the_experiment_tier_and_exports_a_real_report(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with TestClient(_app(tmp_path, workspace)) as client:
        health = client.get("/api/health").json()
        experiments = client.get("/api/evals/experiments")
        created = client.post(
            "/api/evals/runs",
            json={
                # State-machine fixtures only: the experiment tier is exercised
                # without depending on a model or a browser being installed.
                "experiment_id": "loop_limit_8_vs_15",
                "tier": "experiment",
                "phase": "pilot",
            },
        )
        run_id = created.json()["run"]["id"]
        started = client.post(f"/api/evals/runs/{run_id}")

        deadline = monotonic() + 30
        status: dict[str, object] | None = None
        while monotonic() < deadline:
            status = cast(
                dict[str, object],
                client.get(f"/api/evals/runs/{run_id}").json(),
            )
            status_run = cast(dict[str, object], status["run"])
            if status_run["status"] in {"completed", "blocked", "failed"}:
                break
            sleep(0.01)
        reports = client.get("/api/evals/reports").json()["reports"]
        exported = client.get(f"/api/evals/reports/{run_id}").json()["report"]
        snapshot = client.get("/api/ui/evals").json()

    assert health["capabilities"]["eval_runner"] is True
    assert experiments.status_code == 200
    assert {item["id"] for item in experiments.json()["experiments"]} >= {"loop_limit_8_vs_15"}
    assert created.status_code == 201
    assert started.status_code == 202
    assert status is not None
    status_run = cast(dict[str, object], status["run"])
    assert status_run["status"] == "completed"
    assert status_run["reason_code"] is None
    # Two arms, 15 pilot cases each.
    assert len(cast(list[object], status["cases"])) == 30
    assert exported["payload"]["status"] == "completed"
    assert reports == [exported]
    assert snapshot["runs"] == [status_run]
    assert snapshot["reports"] == reports
    assert "events" not in snapshot


def test_eval_cancel_endpoint_and_sanitized_regression_draft(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with TestClient(_app(tmp_path, workspace)) as client:
        conversation_id = client.post(
            "/api/conversations", json={"workspace_root": str(workspace)}
        ).json()["conversation"]["id"]
        client.post(
            f"/api/conversations/{conversation_id}/requests",
            json={"content": "sensitive operator request"},
        )
        deadline = monotonic() + 2
        chat: dict[str, object] | None = None
        while monotonic() < deadline:
            chat = cast(
                dict[str, object],
                client.get("/api/ui/chat", params={"conversation_id": conversation_id}).json(),
            )
            if cast(list[object], chat["turns"]) and chat["active_turn"] is None:
                break
            sleep(0.01)
        assert chat is not None
        turns = cast(list[dict[str, object]], chat["turns"])
        turn_id = cast(str, turns[0]["id"])
        feedback = client.post(
            f"/api/conversations/{conversation_id}/feedback",
            json={
                "rating": -1,
                "comment": "private failure details",
                "turn_id": turn_id,
            },
        ).json()["feedback"]
        draft = client.post(
            "/api/evals/regression-drafts",
            json={"conversation_id": conversation_id, "feedback_id": feedback["id"]},
        )
        drafts = client.get("/api/evals/regression-drafts").json()["drafts"]

        run = client.post(
            "/api/evals/runs",
            json={
                "experiment_id": "thinking_ollama",
                "tier": "experiment",
                "phase": "pilot",
            },
        ).json()["run"]
        canceled = client.post(f"/api/evals/runs/{run['id']}/cancel")

    serialized = draft.text
    assert draft.status_code == 201
    assert drafts == [draft.json()["draft"]]
    assert "private failure details" not in serialized
    assert "sensitive operator request" not in serialized
    assert conversation_id not in serialized
    assert drafts[0]["rating"] == -1
    assert drafts[0]["terminal_outcome_kind"] == "completed"
    assert canceled.status_code == 202
    assert canceled.json()["run"]["status"] == "canceled"
