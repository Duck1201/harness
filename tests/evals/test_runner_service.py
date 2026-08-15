import asyncio
from collections.abc import Mapping
from pathlib import Path

from harness import JsonValue, ToolResultStatus, load_config
from harness.evals import (
    BenchmarkLease,
    CaseRunner,
    CaseRunResult,
    ContractCaseRunner,
    EvalCaseSpec,
    EvalEvidence,
    EvalPhase,
    EvalRunStatus,
    EvalService,
    EvalStore,
    EvalTier,
    TaskVerdict,
    load_eval_catalog,
)

ROOT = Path(__file__).parents[2]


class PassingRunner(CaseRunner):
    def __init__(self) -> None:
        self.specs: list[EvalCaseSpec] = []

    def supports(self, fixture_type: str) -> bool:
        del fixture_type
        return True

    async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult:
        self.specs.append(spec)
        return CaseRunResult(
            evidence=EvalEvidence(),
            metrics={"latency_ms": 1.0},
        )


def _catalog():  # pyright: ignore[reportUnknownParameterType, reportMissingReturnType]
    return load_eval_catalog(
        ROOT / "evals/fixtures/regressions.json",
        ROOT / "evals/experiments.json",
        contract_root=ROOT,
    )


def test_contract_runner_uses_real_executor_for_deterministic_fixture() -> None:
    async def scenario() -> None:
        fixture = next(
            item for item in _catalog().dataset.fixtures if item.id == "reject_absolute_file_path"
        )
        runner = ContractCaseRunner(config=load_config())

        result = await runner.run_case(
            EvalCaseSpec(
                run_id="run",
                arm_id="contract",
                fixture=fixture,
                seed=1,
                order_index=0,
                tier=EvalTier.CONTRACT,
            )
        )

        assert result.evidence.tool_results[0].status is ToolResultStatus.BLOCKED
        assert result.evidence.tool_results[0].error is not None
        assert result.evidence.tool_results[0].error["code"] == "absolute_path_not_allowed"
        assert result.evaluation is not None
        assert result.evaluation.verdict is TaskVerdict.PASS

    asyncio.run(scenario())


def test_contract_runner_executes_all_required_deterministic_fixture_types() -> None:
    async def scenario() -> None:
        runner = ContractCaseRunner(config=load_config())
        required_types = {
            "executor_contract",
            "state_machine",
            "context_builder",
            "privacy_gate",
            "corpus_contract",
        }
        fixtures = [
            fixture for fixture in _catalog().dataset.fixtures if fixture.type in required_types
        ]

        assert {fixture.type for fixture in fixtures} == required_types
        assert all(runner.supports(fixture.type) for fixture in fixtures)
        verdicts: dict[str, TaskVerdict] = {}
        for order_index, fixture in enumerate(fixtures):
            result = await runner.run_case(
                EvalCaseSpec(
                    run_id="run",
                    arm_id="contract",
                    fixture=fixture,
                    seed=1,
                    order_index=order_index,
                    tier=EvalTier.CONTRACT,
                )
            )
            assert result.evaluation is not None
            assert result.evaluation.verdict is not TaskVerdict.NOT_EVALUATED
            verdicts[fixture.id] = result.evaluation.verdict
        assert set(verdicts.values()) == {TaskVerdict.PASS}

    asyncio.run(scenario())


def test_missing_model_runner_blocks_without_simulated_cases(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = EvalService(
            store=EvalStore(tmp_path / "evals.sqlite3"),
            catalog=_catalog(),
            lease=BenchmarkLease(),
            runners={EvalTier.CONTRACT: ContractCaseRunner(config=load_config())},
        )
        await service.initialize()
        run = await service.create_run(
            experiment_id="temperature_local",
            tier=EvalTier.EXPERIMENT,
            phase=EvalPhase.PILOT,
        )

        await service.start(run.id)
        blocked = await service.wait(run.id)

        assert blocked.status is EvalRunStatus.BLOCKED
        assert blocked.reason_code == "runner_not_configured"
        assert await service.store.list_cases(run.id) == []
        assert await service.store.list_metrics(run.id) == []
        await service.shutdown()

    asyncio.run(scenario())


def test_pilot_and_promotion_sizes_and_seeds_are_recorded(tmp_path: Path) -> None:
    async def scenario() -> None:
        runner = PassingRunner()
        service = EvalService(
            store=EvalStore(tmp_path / "evals.sqlite3"),
            catalog=_catalog(),
            lease=BenchmarkLease(),
            runners={EvalTier.EXPERIMENT: runner},
        )
        await service.initialize()
        pilot = await service.create_run(
            experiment_id="thinking_ollama",
            tier=EvalTier.EXPERIMENT,
            phase=EvalPhase.PILOT,
        )
        await service.start(pilot.id)
        assert (await service.wait(pilot.id)).status is EvalRunStatus.COMPLETED
        pilot_cases = await service.store.list_cases(pilot.id)
        assert len(pilot_cases) == 15 * 2
        assert {case.seed for case in pilot_cases}.issubset(set(pilot.seeds))

        promotion = await service.create_run(
            experiment_id="thinking_ollama",
            tier=EvalTier.EXPERIMENT,
            phase=EvalPhase.PROMOTION,
        )
        await service.start(promotion.id)
        assert (await service.wait(promotion.id)).status is EvalRunStatus.COMPLETED
        promotion_cases = await service.store.list_cases(promotion.id)
        assert len(promotion_cases) == 50 * 2
        for arm in await service.store.list_arms(promotion.id):
            arm_cases = [case for case in promotion_cases if case.arm_id == arm.id]
            assert len(arm_cases) == 50
            assert {case.seed for case in arm_cases} == set(promotion.seeds)
        assert len(promotion.seeds) == 3

        report = await service.report(promotion.id)
        assert report.payload["seeds"] == list(promotion.seeds)
        await service.shutdown()

    asyncio.run(scenario())


def test_model_smoke_covers_every_supported_fixture_in_each_recorded_order(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runner = PassingRunner()
        catalog = _catalog()
        service = EvalService(
            store=EvalStore(tmp_path / "evals.sqlite3"),
            catalog=catalog,
            lease=BenchmarkLease(),
            runners={EvalTier.MODEL_SMOKE: runner},
        )
        await service.initialize()

        # No entry in the manifest: a smoke run is the corpus, not an arm comparison.
        run = await service.create_run(
            experiment_id="model_smoke",
            tier=EvalTier.MODEL_SMOKE,
            phase=EvalPhase.PILOT,
        )
        await service.start(run.id)
        assert (await service.wait(run.id)).status is EvalRunStatus.COMPLETED

        cases = await service.store.list_cases(run.id)
        expected = {fixture.id for fixture in catalog.dataset.fixtures}
        assert {case.fixture_id for case in cases} == expected
        assert len(cases) == len(expected) * len(run.seeds)
        for seed in run.seeds:
            assert {case.fixture_id for case in cases if case.seed == seed} == expected
        arms = await service.store.list_arms(run.id)
        assert [arm.arm_id for arm in arms] == ["model_smoke"]
        await service.shutdown()

    asyncio.run(scenario())


def test_cancel_releases_benchmark_lease(tmp_path: Path) -> None:
    class BlockingRunner(PassingRunner):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()

        async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult:
            del spec
            self.entered.set()
            await asyncio.Future[None]()
            raise AssertionError("unreachable")

    async def scenario() -> None:
        runner = BlockingRunner()
        lease = BenchmarkLease()
        service = EvalService(
            store=EvalStore(tmp_path / "evals.sqlite3"),
            catalog=_catalog(),
            lease=lease,
            runners={EvalTier.EXPERIMENT: runner},
        )
        await service.initialize()
        run = await service.create_run(
            experiment_id="thinking_ollama",
            tier=EvalTier.EXPERIMENT,
            phase=EvalPhase.PILOT,
        )
        await service.start(run.id)
        await asyncio.wait_for(runner.entered.wait(), timeout=1)

        canceled = await service.cancel(run.id)

        assert canceled.status is EvalRunStatus.CANCELED
        assert lease.owner is None
        await lease.enter_chat("chat-after-cancel")
        await lease.leave_chat("chat-after-cancel")
        await service.shutdown()

    asyncio.run(scenario())


def test_arm_settings_reach_the_runner_instead_of_dying_in_the_store(tmp_path: Path) -> None:
    """Two arms declared different have to run different, or the comparison is noise."""

    class RecordingRunner:
        def __init__(self) -> None:
            self.settings: list[Mapping[str, JsonValue]] = []

        def supports(self, fixture_type: str) -> bool:
            return fixture_type == "model_task"

        async def run_case(self, spec: EvalCaseSpec) -> CaseRunResult:
            self.settings.append(dict(spec.settings))
            return CaseRunResult(evidence=EvalEvidence(), metrics={})

    async def scenario() -> None:
        runner = RecordingRunner()
        service = EvalService(
            store=EvalStore(tmp_path / "evals.sqlite3"),
            catalog=_catalog(),
            lease=BenchmarkLease(),
            runners={EvalTier.EXPERIMENT: runner},
        )
        await service.initialize()
        try:
            run = await service.create_run(
                experiment_id="model_view_serialization",
                tier=EvalTier.EXPERIMENT,
                phase=EvalPhase.PILOT,
            )
            await service.start(run.id)
            status = EvalRunStatus.RUNNING
            for _ in range(600):
                status = (await service.status(run.id)).status
                if status in {
                    EvalRunStatus.COMPLETED,
                    EvalRunStatus.BLOCKED,
                    EvalRunStatus.FAILED,
                }:
                    break
                await asyncio.sleep(0.01)
            assert status is EvalRunStatus.COMPLETED, status
        finally:
            await service.shutdown()

        formats = sorted({str(item.get("model_view_format")) for item in runner.settings})
        assert formats == ["json", "xml"]

    asyncio.run(scenario())
