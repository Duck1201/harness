#!/usr/bin/env python
"""Runs one experiment from the manifest and prints its report.

The campaign has to be reproducible from the repository, not from a REPL: this
drives the same EvalService the web surface drives, with the same protocol —
15 cases per arm for a pilot, 50 for a promotion, three recorded seeds.

    uv run python scripts/run-experiment.py guarded_web_brave_escalation --phase pilot
    uv run python scripts/run-experiment.py --tier model_smoke
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from harness import (  # noqa: E402
    BenchmarkLease,
    EngineReadiness,
    EvalPhase,
    EvalRunStatus,
    EvalStore,
    EvalTier,
    HuggingFaceTokenEstimator,
    OllamaRuntime,
    load_config,
)
from harness.brave_browser import BraveEgressGuard  # noqa: E402
from harness.evals import (  # noqa: E402
    BrowserBenchCaseRunner,
    CompositeCaseRunner,
    ContractCaseRunner,
    EvalService,
    ModelCaseRunner,
    load_eval_catalog,
)


async def run(
    experiment_id: str,
    phase: EvalPhase,
    tier: EvalTier,
    tokenizer: Path,
    database: Path,
) -> int:
    config = load_config()
    catalog = load_eval_catalog(
        ROOT / "evals/fixtures/regressions.json",
        ROOT / "evals/experiments.json",
        contract_root=ROOT,
    )
    runtime = OllamaRuntime(
        base_url="http://127.0.0.1:11434",
        model=config.runtime_profile.model.id,
        expected_digest=config.runtime_profile.profile_digest_sha256,
    )
    verification = await runtime.verify_profile()
    if not verification.ready:
        print(f"RuntimeProfile is not ready: {verification.reason_code}", file=sys.stderr)
        return 2
    browser = await BraveEgressGuard().readiness()
    if not browser.ready:
        print(f"Browser is not ready: {browser.reason_code}", file=sys.stderr)
        return 2

    estimator = HuggingFaceTokenEstimator(
        tokenizer,
        expected_sha256=hashlib.sha256(tokenizer.read_bytes()).hexdigest(),
    )
    guard = BraveEgressGuard()
    registry = config.tool_registry
    live = CompositeCaseRunner(
        (
            ContractCaseRunner(registry=registry),
            BrowserBenchCaseRunner(registry=registry, browser_guard=guard),
            ModelCaseRunner(
                config=config,
                runtime=runtime,
                estimator=estimator,
                runtime_readiness=EngineReadiness(ready=True),
                browser_guard=guard,
            ),
        )
    )
    service = EvalService(
        store=EvalStore(database),
        catalog=catalog,
        lease=BenchmarkLease(),
        runners={EvalTier.EXPERIMENT: live, EvalTier.MODEL_SMOKE: live},
    )
    await service.initialize()
    try:
        run = await service.create_run(
            experiment_id=experiment_id,
            tier=tier,
            phase=phase,
        )
        print(
            f"run {run.id} seeds={list(run.seeds)} tier={tier.value} phase={phase.value}",
            file=sys.stderr,
        )
        await service.start(run.id)
        while True:
            current = await service.status(run.id)
            if current.status in {
                EvalRunStatus.COMPLETED,
                EvalRunStatus.BLOCKED,
                EvalRunStatus.FAILED,
                EvalRunStatus.CANCELED,
            }:
                break
            await asyncio.sleep(1)
        report = await service.report(run.id)
        print(json.dumps(report.payload, indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if current.status is EvalRunStatus.COMPLETED else 1
    finally:
        await service.shutdown()
        await runtime.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "experiment_id",
        nargs="?",
        default=EvalTier.MODEL_SMOKE.value,
        help='experiment from the manifest, or "model_smoke" for the whole corpus',
    )
    parser.add_argument("--phase", choices=[phase.value for phase in EvalPhase], default="pilot")
    parser.add_argument(
        "--tier",
        choices=[EvalTier.MODEL_SMOKE.value, EvalTier.EXPERIMENT.value],
        default=EvalTier.EXPERIMENT.value,
        help="model_smoke runs every fixture that has a runner, in one arm",
    )
    parser.add_argument(
        "--tokenizer",
        type=Path,
        default=ROOT / ".harness/tokenizer.json",
        help="HuggingFace tokenizer.json used for the context budget",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="EvalStore database; a temporary file by default",
    )
    arguments = parser.parse_args()
    if not arguments.tokenizer.is_file():
        print(f"tokenizer not found: {arguments.tokenizer}", file=sys.stderr)
        return 2
    if arguments.database is not None:
        return asyncio.run(
            run(
                arguments.experiment_id,
                EvalPhase(arguments.phase),
                EvalTier(arguments.tier),
                arguments.tokenizer,
                arguments.database,
            )
        )
    with tempfile.TemporaryDirectory(prefix="harness-eval-") as temporary:
        return asyncio.run(
            run(
                arguments.experiment_id,
                EvalPhase(arguments.phase),
                EvalTier(arguments.tier),
                arguments.tokenizer,
                Path(temporary) / "evals.sqlite3",
            )
        )


if __name__ == "__main__":
    raise SystemExit(main())
