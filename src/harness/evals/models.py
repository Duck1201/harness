from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain import JsonValue, TerminalOutcomeKind, ToolResultStatus


class EvalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_EVALUATED = "not_evaluated"


class EvalTier(StrEnum):
    CONTRACT = "contract"
    MODEL_SMOKE = "model_smoke"
    EXPERIMENT = "experiment"


class EvalPhase(StrEnum):
    PILOT = "pilot"
    PROMOTION = "promotion"


class EvalRunStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELING = "canceling"
    CANCELED = "canceled"
    BLOCKED = "blocked"
    FAILED = "failed"


class DigestContract(EvalModel):
    algorithm: Literal["sha256"]
    canonicalization: Literal["sorted_keys_compact_utf8"]
    scope: tuple[str, ...]


class ToolCalled(EvalModel):
    operator: Literal["tool_called"]
    tool: str = Field(min_length=1)


class ToolNotCalled(EvalModel):
    operator: Literal["tool_not_called"]
    tool: str = Field(min_length=1)


class MaxToolCalls(EvalModel):
    """Bounds the calls a Turn spends, optionally only those of one effect class.

    Without ``effect`` this is every effective call, which is the loop bound.
    With one, it is the budget for that class alone: a read inside the workspace
    jail is not the same expense as a write or an egress, and a fixture that
    means "change nothing" should say so instead of counting everything.
    """

    operator: Literal["max_tool_calls"]
    maximum: int = Field(ge=0)
    effect: str | None = None


class TerminalOutcomeIs(EvalModel):
    operator: Literal["terminal_outcome_is"]
    kind: TerminalOutcomeKind
    reason_code: str | None = None


class ResultStatusIs(EvalModel):
    operator: Literal["result_status_is"]
    status: ToolResultStatus
    tool_call_id: str | None = None


class ResultErrorCodeIs(EvalModel):
    operator: Literal["result_error_code_is"]
    code: str = Field(min_length=1)
    tool_call_id: str | None = None


class ResultDataContains(EvalModel):
    """Asserts that some ResultPayload actually surfaced the expected finding.

    The other result operators read the envelope — status, error class, producer.
    This one reads ``data``, which is where a search says which file matched or a
    read says what it returned. Without it, "the tool was called" is all a fixture
    can claim, and a tool that answers nothing useful still passes.
    """

    operator: Literal["result_data_contains"]
    content: str = Field(min_length=1)
    tool_call_id: str | None = None


class ResultProducerIs(EvalModel):
    """Which engine produced a ResultPayload — "web_fetch" for HTTP, "browser" after escalation."""

    operator: Literal["result_producer_is"]
    producer: str = Field(min_length=1)
    tool_call_id: str | None = None


class FileExists(EvalModel):
    operator: Literal["file_exists"]
    path: str = Field(min_length=1)


class FileContentEquals(EvalModel):
    operator: Literal["file_content_equals"]
    path: str = Field(min_length=1)
    content: str


class FileContentContains(EvalModel):
    operator: Literal["file_content_contains"]
    path: str = Field(min_length=1)
    content: str


class PathWithinWorkspace(EvalModel):
    operator: Literal["path_within_workspace"]
    path: str = Field(min_length=1)


class ResponseLanguagePt(EvalModel):
    operator: Literal["response_language_pt"]


type TypedAssertion = Annotated[
    ToolCalled
    | ToolNotCalled
    | MaxToolCalls
    | TerminalOutcomeIs
    | ResultStatusIs
    | ResultErrorCodeIs
    | ResultDataContains
    | ResultProducerIs
    | FileExists
    | FileContentEquals
    | FileContentContains
    | PathWithinWorkspace
    | ResponseLanguagePt,
    Field(discriminator="operator"),
]


class OracleDefinition(EvalModel):
    """An oracle is executable or it is not an oracle.

    Free-form assertions used to live here and scored INCONCLUSIVE, so a fixture
    carrying them never passed and never failed while still looking covered.
    Prose now has exactly one home, ``explanation``, which nothing evaluates.
    """

    model_config = ConfigDict(extra="allow")

    typed_assertions: tuple[TypedAssertion, ...] = Field(min_length=1)
    explanation: tuple[str, ...] = ()


class RegressionFixture(EvalModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    tags: tuple[str, ...]
    origin: str = Field(min_length=1)
    stimulus: Mapping[str, JsonValue]
    oracle: OracleDefinition


class RegressionDataset(EvalModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[2]
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    dataset_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    digest_contract: DigestContract
    fixtures: tuple[RegressionFixture, ...]


class DatasetReference(EvalModel):
    path: str = Field(min_length=1)
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    dataset_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PromotionProtocol(EvalModel):
    model_config = ConfigDict(extra="allow")

    pilot_runs_per_arm: Literal[15]
    promotion_runs_per_arm: Literal[50]
    recorded_orders_or_seeds: Literal[3]
    security_gate: Literal["zero_violations"]


class ExperimentArm(EvalModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)


class ExperimentDefinition(EvalModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    status: str = Field(min_length=1)
    runtime_profile: str = Field(min_length=1)
    execution_route: str = Field(min_length=1)
    fixture_tags: tuple[str, ...]
    arms: tuple[ExperimentArm, ...]


class ExperimentManifest(EvalModel):
    model_config = ConfigDict(extra="allow")

    schema_version: Literal[2]
    manifest_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    manifest_digest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    digest_contract: DigestContract
    dataset: DatasetReference
    contract_digests: Mapping[str, str]
    promotion_protocol: PromotionProtocol
    experiments: tuple[ExperimentDefinition, ...]


class EvalCatalog(EvalModel):
    dataset: RegressionDataset
    manifest: ExperimentManifest
