"""Runners for the tiers that need the real model or the real browser.

``ContractCaseRunner`` covers what a deterministic executor can prove on its own.
Everything else in the corpus — a task the model has to solve, a browser that has
to escalate by symptom — needs the actual RuntimeProfile, and until now no runner
existed for ``EvalTier.MODEL_SMOKE`` or ``EvalTier.EXPERIMENT``: ``EvalService``
looked one up, found nothing, and blocked the run with ``runner_not_configured``.
"""

from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from ..agent_engine import AgentEngine
from ..brave_browser import BraveBrowserCapability, BraveEgressGuard
from ..composite_tools import CompositeToolExecutor
from ..config import HarnessConfig, ToolRegistryConfig
from ..context_builder import ContextBuilder
from ..conversation_store import ConversationStore
from ..domain import (
    MUTATION_EFFECT,
    Grant,
    JsonValue,
    SessionPolicy,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from ..local_tools import RegistryToolExecutor
from ..ports import (
    EngineReadiness,
    ModelRuntime,
    NullEventSink,
    TokenEstimator,
    ToolExecutor,
    ToolSchema,
)
from ..system_prompt import build_system_prompt
from ..web_tools import WebToolExecutor
from .bench import BENCH_HOSTNAME, SEARCH_PATH, BenchEgressGuard, BenchServer
from .language import PortugueseDetector
from .models import RegressionFixture
from .oracles import EvalEvidence, evaluate_oracle
from .runner import CaseRunner, CaseRunResult, EvalCaseSpec, security_violations

_ALL_GRANTS = ("WorkspaceRootGrant", "WriteGrant", "WebAccessGrant")

# The corpus reads the same prompt on every run. Taking the host date here would
# make two runs of the same arm differ by the day they ran, and every recorded
# trace differ from the one before it, for a fact no fixture measures. It becomes
# a field of the fixture the day a fixture depends on the date.
BENCH_DATE = date(2026, 1, 1)

# The fixture names a symptom per URL; the bench serves one page for each symptom.
_BENCH_PATHS = {"readable": "/readable", "js-only": "/js-only"}

# A fixture that declares a network_stub gets its URL rewritten to the bench page
# with that symptom, so a model task never depends on a third-party site.
_STUB_PAGES = {
    "known_url_returns_readable_html": "/readable",
    "known_url_requires_browser": "/js-only",
}
_URL_PATTERN = re.compile(r"https?://\S+")

# Not a credential: the bench never checks it, it only has to be non-empty so the
# executor takes the configured provider path instead of reporting no credentials.
_BENCH_SEARCH_KEY = "bench-search-key"


def _eval_policy(permissions: Sequence[str] = _ALL_GRANTS) -> SessionPolicy:
    now = datetime.now(UTC)
    return SessionPolicy(
        conversation_id="eval",
        grants=tuple(
            Grant(
                id=f"eval-{permission}",
                conversation_id="eval",
                permission=permission,
                scope="workspace" if permission != "WebAccessGrant" else "public-network",
                granted_at=now,
            )
            for permission in permissions
        ),
    )


class CompositeCaseRunner:
    """Routes each fixture to the first runner that supports its type."""

    def __init__(self, runners: Sequence[CaseRunner]) -> None:
        if not runners:
            raise ValueError("a composite runner needs at least one runner")
        self._runners = tuple(runners)

    def supports(self, fixture_type: str) -> bool:
        return any(runner.supports(fixture_type) for runner in self._runners)

    async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult:
        for runner in self._runners:
            if runner.supports(spec.fixture.type):
                return await runner.run_case(spec)
        raise ValueError(f"no runner supports fixture type: {spec.fixture.type}")


class BrowserBenchCaseRunner:
    """Replays a web_fetch automation fixture against the deterministic bench.

    The fixture states the symptom per URL (how many characters HTTP alone
    extracts); the bench serves one page above the escalation threshold and one
    below it, and the assertions read back which engine produced each result.
    """

    _SUPPORTED_TYPES = frozenset({"executor_automation", "egress_boundary"})

    def __init__(
        self,
        *,
        registry: ToolRegistryConfig,
        browser_guard: BraveEgressGuard | None = None,
    ) -> None:
        self._registry = registry
        self._browser_guard = browser_guard or BraveEgressGuard()

    def supports(self, fixture_type: str) -> bool:
        return fixture_type in self._SUPPORTED_TYPES

    async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult:
        if spec.fixture.type == "egress_boundary":
            return await self._run_boundary(spec)
        calls_spec = spec.fixture.stimulus.get("calls")
        if not isinstance(calls_spec, Sequence) or isinstance(calls_spec, str):
            raise ValueError(f"fixture has no bench calls: {spec.fixture.id}")

        started = time.monotonic()
        async with BenchServer() as bench:
            guard = BenchEgressGuard(bench.port)
            executor = WebToolExecutor(
                registry=self._registry,
                session_policy=_eval_policy(("WebAccessGrant",)),
                egress_guard=guard,
                browser_capability=BraveBrowserCapability(
                    egress_guard=guard,
                    guard=self._browser_guard,
                ),
                browser_egress_guard=self._browser_guard,
            )
            calls: list[ToolCall] = []
            results: list[ToolResult] = []
            for index, raw in enumerate(calls_spec):
                if not isinstance(raw, Mapping):
                    raise ValueError(f"invalid bench call in fixture: {spec.fixture.id}")
                entry = cast(Mapping[str, JsonValue], raw)
                call = ToolCall(
                    id=f"bench-{index}",
                    name="web_fetch",
                    arguments={"url": bench.url(self._bench_path(entry))},
                )
                calls.append(call)
                results.append(await executor.execute(call))
            bench_requests = bench.requests

        evidence = EvalEvidence(
            tool_calls=tuple(calls),
            tool_results=tuple(results),
        )
        producers = [str(result.meta.get("producer", "")) for result in results]
        evaluation = evaluate_oracle(
            spec.fixture.oracle.typed_assertions,
            evidence,
            tool_effects=self._registry.effects_by_tool,
        )
        return CaseRunResult(
            evidence=evidence,
            metrics={
                "latency_ms": (time.monotonic() - started) * 1000,
                "browser_escalations": float(producers.count("browser")),
                "data_egress_events": float(len(bench_requests)),
                "untrusted_web_taint_violations": float(
                    sum(
                        "UntrustedWebTaint" not in cast(list[str], result.meta.get("taints", []))
                        for result in results
                        if result.status is ToolResultStatus.SUCCESS
                    )
                ),
            },
            security_violations=security_violations(spec.fixture, evaluation),
            evaluation=evaluation,
        )

    async def _run_boundary(self, spec: EvalCaseSpec) -> CaseRunResult:
        """Proves the bench exception does not exist on the production route.

        The grant is present and the guard is the production one, so the only
        thing that can refuse the bench address is the egress policy itself.
        """
        raw_call = spec.fixture.stimulus.get("tool_call")
        if not isinstance(raw_call, Mapping):
            raise ValueError(f"boundary fixture has no tool_call: {spec.fixture.id}")
        entry = cast(Mapping[str, JsonValue], raw_call)
        raw_arguments = entry.get("arguments")
        arguments: Mapping[str, JsonValue] = (
            cast(Mapping[str, JsonValue], raw_arguments)
            if isinstance(raw_arguments, Mapping)
            else {}
        )
        call = ToolCall(
            id="boundary-0",
            name=str(entry.get("name", "web_fetch")),
            arguments=arguments,
        )
        executor = WebToolExecutor(
            registry=self._registry,
            session_policy=_eval_policy(("WebAccessGrant",)),
        )
        result = await executor.execute(call)
        evidence = EvalEvidence(tool_calls=(call,), tool_results=(result,))
        evaluation = evaluate_oracle(
            spec.fixture.oracle.typed_assertions,
            evidence,
            tool_effects=self._registry.effects_by_tool,
        )
        return CaseRunResult(
            evidence=evidence,
            metrics={"data_egress_events": 0.0},
            security_violations=security_violations(spec.fixture, evaluation),
            evaluation=evaluation,
        )

    def _bench_path(self, entry: Mapping[str, JsonValue]) -> str:
        url = entry.get("url")
        if not isinstance(url, str):
            raise ValueError("bench call has no url")
        tail = urlsplit(url).path.rsplit("/", 1)[-1]
        path = _BENCH_PATHS.get(tail)
        if path is None:
            raise ValueError(f"no bench page for {url}")
        return path


class ModelCaseRunner:
    """Runs a fixture's user request through the real AgentEngine and RuntimeProfile."""

    _SUPPORTED_TYPES = frozenset({"model_task", "loop_recovery", "capability_gate"})

    def __init__(
        self,
        *,
        config: HarnessConfig,
        runtime: ModelRuntime,
        estimator: TokenEstimator,
        runtime_readiness: EngineReadiness | None = None,
        browser_guard: BraveEgressGuard | None = None,
    ) -> None:
        self._config = config
        self._runtime = runtime
        self._estimator = estimator
        self._runtime_readiness = runtime_readiness or EngineReadiness(ready=True)
        self._browser_guard = browser_guard or BraveEgressGuard()
        self._language_detector = PortugueseDetector()

    def supports(self, fixture_type: str) -> bool:
        return fixture_type in self._SUPPORTED_TYPES

    async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult:
        raw_request = spec.fixture.stimulus.get("user_request")
        if not isinstance(raw_request, str):
            raise ValueError(f"fixture has no user_request: {spec.fixture.id}")

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="he-model-") as temporary:
            base = Path(temporary)
            workspace = base / "w"
            workspace.mkdir()
            _seed_workspace(workspace, spec.fixture)

            async with BenchServer() as bench:
                request = _bench_request(raw_request, spec.fixture, bench)
                store = ConversationStore(base / "c.sqlite3")
                await store.initialize()
                revision = await store.create_workspace(str(workspace))
                conversation = await store.create_conversation(revision.workspace_id)
                # No Operator watches a bench run, and the default gate denies, so
                # every fixture that writes would measure a refusal instead of the
                # model. The waiver is the Operator's own mechanism, used here in
                # the open rather than a gate that only evals have.
                await store.waive_confirmation(conversation.id, MUTATION_EFFECT)
                policy = _eval_policy()
                executor = self._executor(workspace, policy, bench)
                engine = AgentEngine(
                    store=store,
                    runtime=self._runtime,
                    tool_executor=executor,
                    context_builder=ContextBuilder(
                        self._estimator,
                        context_window=_CONTEXT_WINDOW,
                        output_budget=self._config.loop.max_output_tokens,
                    ),
                    event_sink=NullEventSink(),
                    system_prompt=build_system_prompt(self._config, today=BENCH_DATE),
                    tool_schemas=self._tool_schemas(policy),
                    model_options={
                        "temperature": self._config.execution_route.sampling.temperature,
                        "presence_penalty": (
                            self._config.execution_route.sampling.presence_penalty
                        ),
                    },
                    seed=spec.seed,
                    max_model_invocations=self._config.loop.max_steps,
                    max_tool_calls_per_step=self._config.loop.max_tool_calls_per_step,
                    max_tool_calls_per_turn=self._config.loop.max_tool_calls_per_turn,
                    max_read_calls_per_turn=self._config.loop.max_read_calls_per_turn,
                    tool_effects=self._config.tool_registry.effects_by_tool,
                    max_turn_duration_seconds=self._config.loop.max_turn_duration_seconds,
                    runtime_readiness=self._runtime_readiness,
                )
                turn = await engine.run(conversation.id, request)

            steps = await store.list_agent_steps(turn.id)
            history = await store.list_canonical_history(conversation.id)
            calls = tuple(call for step in steps for call in step.tool_calls)
            results = tuple(result for step in steps for result in step.tool_results)
            response = next(
                (
                    str(entry.payload.get("content", ""))
                    for entry in reversed(history)
                    if entry.kind.value == "final_response"
                ),
                None,
            )
            outcome = turn.terminal_outcome
            evidence = EvalEvidence(
                tool_calls=calls,
                tool_results=results,
                terminal_outcome_kind=outcome.kind if outcome is not None else None,
                terminal_outcome_reason=outcome.reason_code if outcome is not None else None,
                terminal_outcome_detail=outcome.detail if outcome is not None else None,
                workspace_root=workspace,
                observed_paths=(),
                response=response,
            )
            evaluation = evaluate_oracle(
                spec.fixture.oracle.typed_assertions,
                evidence,
                language_detector=self._language_detector,
                tool_effects=self._config.tool_registry.effects_by_tool,
            )
            return CaseRunResult(
                evidence=evidence,
                metrics={
                    "latency_ms": (time.monotonic() - started) * 1000,
                    "steps_to_terminal": float(len(steps)),
                    "extra_tool_calls": float(max(0, len(calls) - _expected_calls(spec.fixture))),
                    "tool_noop_rate": _noop_rate(results),
                    # A malformed response or a tool call emitted as prose is caught
                    # by AgentEngine and never reaches the Operator, so no oracle can
                    # see it. Counting it is the only way the corpus reports how often
                    # the model produces something the harness had to throw away.
                    "rejected_model_attempts": float(
                        sum(entry.kind.value == "rejected_model_attempt" for entry in history)
                    ),
                },
                security_violations=security_violations(spec.fixture, evaluation),
                evaluation=evaluation,
            )

    def _tool_schemas(self, policy: SessionPolicy) -> tuple[ToolSchema, ...]:
        effective = policy.effective_grants
        return tuple(
            definition.tool_schema()
            for definition in self._config.tool_registry.model_tools
            if definition.status == "enabled"
            and all(grant in effective for grant in definition.required_grants)
        )

    def _executor(self, workspace: Path, policy: SessionPolicy, bench: BenchServer) -> ToolExecutor:
        registry = self._config.tool_registry
        local: ToolExecutor = RegistryToolExecutor(
            registry=registry,
            workspace_root=workspace,
            session_policy=policy,
        )
        guard = BenchEgressGuard(bench.port)
        web: ToolExecutor = WebToolExecutor(
            registry=registry,
            session_policy=policy,
            egress_guard=guard,
            # The bench answers the provider endpoint, so web_search is offered and
            # exercised without the corpus depending on a real Brave key.
            brave_api_key=_BENCH_SEARCH_KEY,
            search_endpoint=bench.url(SEARCH_PATH),
            browser_capability=BraveBrowserCapability(
                egress_guard=guard,
                guard=self._browser_guard,
            ),
            browser_egress_guard=self._browser_guard,
        )
        return CompositeToolExecutor(
            routes={
                definition.name: (web if definition.name.startswith("web_") else local)
                for definition in registry.model_tools
            }
        )


# harness.json#context.initial_budget_tokens, the same value ApplicationService uses.
_CONTEXT_WINDOW = 24576


def _bench_request(request: str, fixture: RegressionFixture, bench: BenchServer) -> str:
    stub = fixture.stimulus.get("network_stub")
    path = _STUB_PAGES.get(stub) if isinstance(stub, str) else None
    if path is None:
        return request
    return _URL_PATTERN.sub(bench.url(path), request)


def _seed_workspace(workspace: Path, fixture: RegressionFixture) -> None:
    entries = fixture.stimulus.get("workspace")
    if not isinstance(entries, Sequence) or isinstance(entries, str):
        return
    for raw in entries:
        # A fixture describes its workspace either as "path" or as
        # {"path": ..., "content": ...}; both have to produce a real file, or the
        # model is asked to search an empty directory and loops until the limit.
        if isinstance(raw, str):
            name, content = _split_seed_entry(raw)
        elif isinstance(raw, Mapping):
            entry = cast(Mapping[str, JsonValue], raw)
            raw_name = entry.get("path")
            if not isinstance(raw_name, str):
                continue
            raw_content = entry.get("content")
            name = raw_name
            content = raw_content if isinstance(raw_content, str) else ""
        else:
            continue
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            continue
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _split_seed_entry(entry: str) -> tuple[str, str]:
    """Splits "src/app.js contains TODO: validate input" into path and content."""
    path, separator, description = entry.partition(" ")
    if not separator:
        return entry.strip(), ""
    return path.strip(), description.strip()


def _expected_calls(fixture: RegressionFixture) -> int:
    extra = fixture.oracle.model_extra or {}
    maximum = extra.get("max_tool_calls")
    return maximum if isinstance(maximum, int) else 0


def _noop_rate(results: Sequence[ToolResult]) -> float:
    if not results:
        return 0.0
    empty = sum(result.status is ToolResultStatus.EMPTY for result in results)
    return empty / len(results)


__all__ = [
    "BENCH_HOSTNAME",
    "BrowserBenchCaseRunner",
    "CompositeCaseRunner",
    "ModelCaseRunner",
]
