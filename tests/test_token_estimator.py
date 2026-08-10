import hashlib
import json
from pathlib import Path

import pytest

from harness import HuggingFaceTokenEstimator, ModelMessage, ModelRole, ToolSchema


def _write_word_level_tokenizer(path: Path) -> str:
    payload: dict[str, object] = {
        "version": "1.0",
        "truncation": None,
        "padding": None,
        "added_tokens": [],
        "normalizer": None,
        "pre_tokenizer": {"type": "Whitespace"},
        "post_processor": None,
        "decoder": None,
        "model": {
            "type": "WordLevel",
            "vocab": {
                "[UNK]": 0,
                "system": 1,
                "hello": 2,
                "read_file": 3,
                "Read": 4,
                "a": 5,
                "file": 6,
                "type": 7,
                "object": 8,
            },
            "unk_token": "[UNK]",
        },
    }
    content = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode()).hexdigest()


def test_local_tokenizer_is_digest_validated_and_estimates_messages_and_schemas(
    tmp_path: Path,
) -> None:
    tokenizer_path = tmp_path / "tokenizer.json"
    digest = _write_word_level_tokenizer(tokenizer_path)
    estimator = HuggingFaceTokenEstimator(
        tokenizer_path,
        expected_sha256=digest,
        base_overhead=2,
        message_overhead=3,
        schema_overhead=5,
    )

    estimate = estimator.estimate(
        (ModelMessage(role=ModelRole.SYSTEM, content="hello"),),
        (ToolSchema("read_file", "Read a file", {"type": "object"}),),
    )

    assert estimator.validated is True
    assert estimator.readiness.ready is True
    assert estimate == 36


@pytest.mark.parametrize(
    ("create_file", "expected", "reason_code"),
    [
        (False, "0" * 64, "tokenizer_file_missing"),
        (True, "0" * 64, "tokenizer_digest_mismatch"),
    ],
)
def test_local_tokenizer_is_not_validated_when_missing_or_digest_differs(
    tmp_path: Path,
    create_file: bool,
    expected: str,
    reason_code: str,
) -> None:
    tokenizer_path = tmp_path / "tokenizer.json"
    if create_file:
        _write_word_level_tokenizer(tokenizer_path)

    estimator = HuggingFaceTokenEstimator(tokenizer_path, expected_sha256=expected)

    assert estimator.validated is False
    assert estimator.readiness.ready is False
    assert estimator.readiness.reason_code == reason_code
    with pytest.raises(RuntimeError, match="not validated"):
        estimator.estimate((), ())


def test_invalid_local_tokenizer_has_clear_readiness(tmp_path: Path) -> None:
    tokenizer_path = tmp_path / "tokenizer.json"
    content = b"not-json"
    tokenizer_path.write_bytes(content)

    estimator = HuggingFaceTokenEstimator(
        tokenizer_path,
        expected_sha256=hashlib.sha256(content).hexdigest(),
    )

    assert estimator.validated is False
    assert estimator.readiness.reason_code == "tokenizer_file_invalid"
