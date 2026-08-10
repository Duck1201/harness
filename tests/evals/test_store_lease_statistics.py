import asyncio
from pathlib import Path

import pytest

from harness import TerminalOutcomeKind
from harness.evals import (
    BenchmarkLease,
    BenchmarkLeaseCanceled,
    EvalPhase,
    EvalRunStatus,
    EvalStore,
    EvalTier,
    TaskVerdict,
    evaluate_promotion_gate,
    paired_bootstrap_difference,
    wilson_interval,
)


def test_eval_store_persists_only_eval_entities_in_a_separate_database(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        database = tmp_path / "evals.sqlite3"
        store = EvalStore(database)
        await store.initialize()
        run = await store.create_run(
            experiment_id="contract_regressions",
            tier=EvalTier.CONTRACT,
            phase=EvalPhase.PILOT,
            seeds=(11, 22, 33),
        )
        arm = await store.create_arm(run.id, "contract", settings={"temperature": 0.0})
        case = await store.record_case(
            run_id=run.id,
            arm_id=arm.id,
            fixture_id="reject_absolute_file_path",
            seed=11,
            order_index=0,
            verdict=TaskVerdict.PASS,
            terminal_outcome_kind=TerminalOutcomeKind.BLOCKED,
            terminal_outcome_reason="invalid_workspace_path",
            security_violations=0,
        )
        metric = await store.record_metric(
            run_id=run.id,
            arm_id=arm.id,
            case_id=case.id,
            name="latency_ms",
            value=3.5,
        )
        report = await store.save_report(
            run.id,
            {
                "run_id": run.id,
                "verdict_counts": {"pass": 1},
                "promoted": False,
                "synthetic_content": "allowed synthetic fixture material",
            },
        )
        finished = await store.set_run_status(run.id, EvalRunStatus.COMPLETED)

        reopened = EvalStore(database)
        await reopened.initialize()
        assert await reopened.get_run(run.id) == finished
        assert await reopened.list_arms(run.id) == [arm]
        assert await reopened.list_cases(run.id) == [case]
        assert await reopened.list_metrics(run.id) == [metric]
        assert await reopened.get_report(run.id) == report
        assert database != tmp_path / "conversations.sqlite3"
        assert report.payload["synthetic_content"] == (
            "allowed synthetic fixture material"
        )

    asyncio.run(scenario())


def test_benchmark_lease_waits_for_active_chat_and_queues_new_chat() -> None:
    async def scenario() -> None:
        lease = BenchmarkLease()
        await lease.enter_chat("chat-active")
        benchmark = asyncio.create_task(lease.acquire("run-1"))
        await asyncio.sleep(0)
        queued_chat = asyncio.create_task(lease.enter_chat("chat-queued"))
        await asyncio.sleep(0)

        assert not benchmark.done()
        assert not queued_chat.done()
        assert lease.waiting_benchmarks == ("run-1",)

        await lease.leave_chat("chat-active")
        await asyncio.wait_for(benchmark, timeout=1)
        assert lease.owner == "run-1"
        assert not queued_chat.done()

        await lease.release("run-1")
        await asyncio.wait_for(queued_chat, timeout=1)
        await lease.leave_chat("chat-queued")
        assert lease.owner is None

    asyncio.run(scenario())


def test_benchmark_lease_can_cancel_a_waiting_acquisition() -> None:
    async def scenario() -> None:
        lease = BenchmarkLease()
        await lease.enter_chat("chat-active")
        benchmark = asyncio.create_task(lease.acquire("run-canceled"))
        await asyncio.sleep(0)

        assert await lease.cancel("run-canceled") is True
        with pytest.raises(BenchmarkLeaseCanceled):
            await benchmark
        await lease.leave_chat("chat-active")

        await lease.enter_chat("next-chat")
        await lease.leave_chat("next-chat")

    asyncio.run(scenario())


def test_wilson_bootstrap_and_promotion_gates_are_deterministic() -> None:
    interval = wilson_interval(5, 10)
    assert interval.estimate == 0.5
    assert interval.lower == pytest.approx(0.2366, abs=0.0001)
    assert interval.upper == pytest.approx(0.7634, abs=0.0001)

    candidate = [True, True, False, True]
    control = [True, False, False, True]
    first = paired_bootstrap_difference(candidate, control, seed=90210, samples=2000)
    second = paired_bootstrap_difference(candidate, control, seed=90210, samples=2000)
    assert first == second
    assert first.estimate == 0.25

    promoted = evaluate_promotion_gate(
        control=(TaskVerdict.PASS, TaskVerdict.FAIL) * 25,
        candidate=(TaskVerdict.PASS, TaskVerdict.PASS) * 25,
        security_violations=0,
        seed=7,
    )
    assert promoted.promoted is True
    assert promoted.non_inferiority_margin == -0.05

    security_failure = evaluate_promotion_gate(
        control=(TaskVerdict.FAIL,) * 50,
        candidate=(TaskVerdict.PASS,) * 50,
        security_violations=1,
        seed=7,
    )
    assert security_failure.promoted is False
    assert security_failure.reason_code == "security_violation"

    inconclusive = evaluate_promotion_gate(
        control=(TaskVerdict.PASS,) * 50,
        candidate=(TaskVerdict.PASS,) * 49 + (TaskVerdict.INCONCLUSIVE,),
        security_violations=0,
        seed=7,
    )
    assert inconclusive.promoted is False
    assert inconclusive.reason_code == "inconclusive_cases"
