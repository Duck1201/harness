import asyncio
from pathlib import Path

import pytest

from harness import load_config
from harness.brave_browser import BraveEgressGuard
from harness.evals import (
    BrowserBenchCaseRunner,
    CompositeCaseRunner,
    ContractCaseRunner,
    EvalCaseSpec,
    EvalTier,
    RegressionFixture,
    TaskVerdict,
    load_eval_catalog,
)
from harness.evals.bench import (
    BENCH_HOSTNAME,
    BROWSER_REQUIRED_PAGE,
    HTTP_READABLE_PAGE,
    BenchEgressGuard,
    BenchServer,
)
from harness.web_tools import EgressPolicyError

brave_required = pytest.mark.skipif(
    BraveEgressGuard().executable is None,
    reason="Brave is not installed on this host",
)


def _fixtures() -> dict[str, RegressionFixture]:
    root = Path(__file__).resolve().parents[2]
    catalog = load_eval_catalog(
        root / "evals/fixtures/regressions.json",
        root / "evals/experiments.json",
        contract_root=root,
    )
    return {fixture.id: fixture for fixture in catalog.dataset.fixtures}


def _spec(fixture: RegressionFixture) -> EvalCaseSpec:
    return EvalCaseSpec(
        run_id="run-1",
        arm_id="arm-1",
        fixture=fixture,
        seed=104729,
        order_index=0,
        tier=EvalTier.EXPERIMENT,
    )


def test_bench_pages_sit_on_both_sides_of_the_extraction_threshold() -> None:
    # The readable page must be usable over HTTP alone; the other one only after
    # JavaScript runs, which is what makes the escalation observable.
    assert len(HTTP_READABLE_PAGE) > 120
    assert b"carregando" in BROWSER_REQUIRED_PAGE
    assert b"<script>" in BROWSER_REQUIRED_PAGE


def test_bench_guard_pins_only_the_bench_hostname() -> None:
    async def scenario() -> None:
        async with BenchServer() as bench:
            guard = BenchEgressGuard(bench.port)

            pinned = await guard.resolve(bench.url("/readable"))
            assert pinned.hostname == BENCH_HOSTNAME
            assert [address.host for address in pinned.addresses] == ["127.0.0.1"]

            # Anything else still goes through the production checks.
            with pytest.raises(EgressPolicyError):
                await guard.resolve("http://127.0.0.1:1/")

    asyncio.run(scenario())


def test_bench_exception_is_refused_on_the_production_route() -> None:
    async def scenario() -> None:
        runner = BrowserBenchCaseRunner(registry=load_config().tool_registry)

        result = await runner.run_case(
            _spec(_fixtures()["eval_bench_exception_is_not_available_in_production"])
        )

        assert result.evaluation is not None
        assert result.evaluation.verdict is TaskVerdict.PASS
        assert result.security_violations == 0
        assert result.metrics["data_egress_events"] == 0.0

    asyncio.run(scenario())


@brave_required
def test_browser_escalation_happens_only_for_the_page_that_needs_it() -> None:
    async def scenario() -> None:
        runner = BrowserBenchCaseRunner(registry=load_config().tool_registry)

        result = await runner.run_case(_spec(_fixtures()["web_http_first_browser_by_symptom"]))

        assert result.evaluation is not None
        assert result.evaluation.verdict is TaskVerdict.PASS
        assert result.metrics["browser_escalations"] == 1.0
        assert result.metrics["untrusted_web_taint_violations"] == 0.0
        assert result.security_violations == 0
        producers = [result_item.meta["producer"] for result_item in result.evidence.tool_results]
        assert producers == ["web_fetch", "browser"]

    asyncio.run(scenario())


def test_composite_routes_each_fixture_type_to_a_runner_that_supports_it() -> None:
    registry = load_config().tool_registry
    contract = ContractCaseRunner(registry=registry)
    bench = BrowserBenchCaseRunner(registry=registry)
    composite = CompositeCaseRunner((contract, bench))

    assert contract.supports("privacy_gate")
    assert not contract.supports("egress_boundary")
    assert bench.supports("egress_boundary")
    assert composite.supports("privacy_gate")
    assert composite.supports("executor_automation")
    assert not composite.supports("model_task")

    with pytest.raises(ValueError):
        CompositeCaseRunner(())
