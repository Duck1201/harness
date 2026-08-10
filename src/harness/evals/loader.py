import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .models import EvalCatalog, ExperimentManifest, RegressionDataset


class EvalCatalogError(ValueError):
    pass


class DatasetDriftError(EvalCatalogError):
    pass


def load_eval_catalog(
    dataset_path: str | Path,
    manifest_path: str | Path,
    *,
    contract_root: str | Path | None = None,
    validate_manifest_digest: bool = True,
    validate_dataset_reference: bool = True,
) -> EvalCatalog:
    dataset = load_regression_dataset(dataset_path)
    manifest = load_experiment_manifest(
        manifest_path,
        contract_root=contract_root,
        validate_digest=validate_manifest_digest,
    )
    if validate_dataset_reference:
        reference = manifest.dataset
        comparisons = {
            "dataset_id": (reference.dataset_id, dataset.dataset_id),
            "dataset_version": (reference.dataset_version, dataset.dataset_version),
            "dataset_digest_sha256": (
                reference.dataset_digest_sha256,
                dataset.dataset_digest_sha256,
            ),
        }
        for field, (observed, expected) in comparisons.items():
            if observed != expected:
                raise DatasetDriftError(
                    f"experiments.dataset.{field} drift: expected {expected}, got {observed}"
                )

    return EvalCatalog(dataset=dataset, manifest=manifest)


def load_regression_dataset(path: str | Path) -> RegressionDataset:
    raw = _read_document(path)
    _validate_digest(raw, "dataset_digest_sha256")
    return RegressionDataset.model_validate(raw)


def load_experiment_manifest(
    path: str | Path,
    *,
    contract_root: str | Path | None = None,
    validate_digest: bool = True,
) -> ExperimentManifest:
    raw = _read_document(path)
    if validate_digest:
        _validate_digest(raw, "manifest_digest_sha256")
    manifest = ExperimentManifest.model_validate(raw)
    if contract_root is not None:
        _validate_contract_digests(manifest, Path(contract_root))
    return manifest


def canonical_digest(value: object) -> str:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_document(path: str | Path) -> dict[str, object]:
    try:
        decoded = cast(object, json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvalCatalogError(f"could not load eval document: {path}") from error
    if not isinstance(decoded, dict):
        raise EvalCatalogError(f"eval document must be an object: {path}")
    return cast(dict[str, object], decoded)


def _validate_digest(document: Mapping[str, object], digest_field: str) -> None:
    contract = document.get("digest_contract")
    if not isinstance(contract, Mapping):
        raise DatasetDriftError("digest_contract is missing")
    typed_contract = cast(Mapping[str, object], contract)
    if typed_contract.get("algorithm") != "sha256":
        raise DatasetDriftError("unsupported digest algorithm")
    if typed_contract.get("canonicalization") != "sorted_keys_compact_utf8":
        raise DatasetDriftError("unsupported digest canonicalization")
    scope = typed_contract.get("scope")
    if not isinstance(scope, list) or not scope:
        raise DatasetDriftError("digest_contract.scope must be a non-empty string array")
    raw_scope = cast(list[object], scope)
    if not all(isinstance(item, str) for item in raw_scope):
        raise DatasetDriftError("digest_contract.scope must be a non-empty string array")
    typed_scope = cast(list[str], raw_scope)
    if len(set(typed_scope)) != len(typed_scope):
        raise DatasetDriftError("digest_contract.scope contains duplicates")
    missing = [field for field in typed_scope if field not in document]
    if missing:
        raise DatasetDriftError(f"digest scope field is missing: {missing[0]}")
    selected = {field: document[field] for field in typed_scope}
    expected = canonical_digest(selected)
    observed = document.get(digest_field)
    if observed != expected:
        raise DatasetDriftError(f"{digest_field} drift: expected {expected}, got {observed}")


def _validate_contract_digests(manifest: ExperimentManifest, root: Path) -> None:
    paths = {
        "model_profiles_sha256": root / "config/model-profiles.json",
        "harness_sha256": root / "config/harness.json",
        "tool_registry_sha256": root / "config/tool-registry.json",
    }
    for field, path in paths.items():
        expected = canonical_digest(_read_document(path))
        observed = manifest.contract_digests.get(field)
        if observed != expected:
            raise DatasetDriftError(
                f"experiments.contract_digests.{field} drift: expected {expected}, got {observed}"
            )
