import asyncio
import json

import httpx
import pytest

from harness import (
    ModelMessage,
    ModelRequest,
    ModelRole,
    OllamaRuntime,
    OllamaRuntimeError,
    ToolSchema,
)


def test_ollama_runtime_sends_non_streaming_chat_and_parses_response() -> None:
    async def scenario() -> None:
        requests: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/chat"
            requests.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "model": "mitos:latest",
                    "created_at": "2026-08-10T12:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "thinking": "I should inspect it",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "read_file",
                                    "arguments": {"file_path": "README.md"},
                                }
                            }
                        ],
                    },
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 100,
                    "load_duration": 10,
                    "prompt_eval_count": 20,
                    "prompt_eval_duration": 30,
                    "eval_count": 8,
                    "eval_duration": 60,
                },
            )

        runtime = OllamaRuntime(
            base_url="http://ollama.test",
            model="mitos:latest",
            expected_digest="abc123",
            transport=httpx.MockTransport(handler),
        )
        response = await runtime.generate(
            ModelRequest(
                messages=(ModelMessage(ModelRole.USER, "read it"),),
                tools=(
                    ToolSchema(
                        "read_file",
                        "Read a file",
                        {"type": "object", "properties": {}},
                    ),
                ),
                options={"temperature": 0.3, "presence_penalty": 0},
                seed=41,
            )
        )
        await runtime.aclose()

        body = requests[0]
        assert body["stream"] is False
        assert body["think"] is True
        assert body["options"] == {
            "temperature": 0.3,
            "presence_penalty": 0,
            "seed": 41,
            "num_predict": 8192,
        }
        assert body["tools"][0]["function"]["name"] == "read_file"  # type: ignore[index]
        assert response.reasoning == "I should inspect it"
        assert response.tool_calls[0].name == "read_file"
        assert response.tool_calls[0].arguments == {"file_path": "README.md"}
        assert response.tool_calls[0].id
        assert response.usage is not None
        assert (response.usage.input_tokens, response.usage.output_tokens) == (20, 8)
        assert response.durations is not None
        assert response.durations.total_ns == 100
        assert response.durations.eval_ns == 60

    asyncio.run(scenario())


def test_ollama_runtime_returns_structured_http_error() -> None:
    async def scenario() -> None:
        runtime = OllamaRuntime(
            base_url="http://ollama.test",
            model="mitos:latest",
            expected_digest="abc123",
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    503,
                    json={"error": "model unavailable"},
                    request=request,
                )
            ),
        )

        with pytest.raises(OllamaRuntimeError) as captured:
            await runtime.generate(
                ModelRequest(
                    messages=(ModelMessage(ModelRole.USER, "hello"),),
                    tools=(),
                    options={},
                    seed=1,
                )
            )
        await runtime.aclose()

        assert captured.value.status_code == 503
        assert captured.value.retryable is True
        assert captured.value.error == {
            "code": "ollama_http_error",
            "message": "model unavailable",
            "status_code": 503,
        }

    asyncio.run(scenario())


def test_ollama_profile_verification_uses_tags_digest() -> None:
    async def scenario() -> None:
        requested_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "name": "mitos:latest",
                            "model": "mitos:latest",
                            "modified_at": "2026-08-10T12:00:00Z",
                            "size": 123,
                            "digest": "sha256:abc123",
                            "details": {},
                        }
                    ]
                },
            )

        matching = OllamaRuntime(
            base_url="http://ollama.test",
            model="mitos:latest",
            expected_digest="abc123",
            transport=httpx.MockTransport(handler),
        )
        assert (await matching.verify_profile()).ready is True
        await matching.aclose()

        mismatched = OllamaRuntime(
            base_url="http://ollama.test",
            model="mitos:latest",
            expected_digest="different",
            transport=httpx.MockTransport(handler),
        )
        verification = await mismatched.verify_profile()
        await mismatched.aclose()

        assert requested_paths == ["/api/tags", "/api/tags"]
        assert verification.ready is False
        assert verification.reason_code == "model_digest_mismatch"
        assert verification.observed_digest == "abc123"

    asyncio.run(scenario())
