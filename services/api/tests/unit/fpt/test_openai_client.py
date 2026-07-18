"""Client-level tests for the OpenAI-compatible FPT wire mapping.

These exercise ``FPTClient`` directly (the HTTP/protocol seam) plus a couple of
gateway integrations that must keep holding once the wire is OpenAI-shaped.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx
import pytest

from creditops.application.ports.model_gateway import (
    InferenceUnavailableError,
    InferenceValidationError,
    KIERequest,
    VisionRequest,
)
from creditops.infrastructure.fpt.catalog import FPTCapabilityConfig, FPTCatalog
from creditops.infrastructure.fpt.client import FPTClient
from creditops.infrastructure.fpt.gateway import FPTInferenceGateway


class RecordingTransport:
    """Return a canned status/body and capture the outgoing request."""

    def __init__(self, *, status: int = 200, body: Any = None, raw: bytes | None = None) -> None:
        self.status = status
        self.body = body
        self.raw = raw
        self.requests: list[httpx.Request] = []
        self.request_json: list[Any] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.request_json.append(json.loads(request.content) if request.content else None)
        if self.raw is not None:
            return httpx.Response(self.status, content=self.raw, request=request)
        return httpx.Response(self.status, json=self.body, request=request)


def _chat_config() -> FPTCapabilityConfig:
    return FPTCapabilityConfig(
        capability="reasoning",
        endpoint_id="reasoning-1",
        model_id="qwen3-benchmark-gated",
        endpoint_url="https://fpt.example/v1/chat/completions",
        api_key="secret-key",
    )


def _embedding_config() -> FPTCapabilityConfig:
    return FPTCapabilityConfig(
        capability="embedding",
        endpoint_id="embedding-1",
        model_id="e5-benchmark-gated",
        endpoint_url="https://fpt.example/v1/embeddings",
        api_key="secret-key",
    )


def _client(config: FPTCapabilityConfig, transport: RecordingTransport) -> FPTClient:
    catalog = FPTCatalog(capabilities={config.capability: config})
    return FPTClient(catalog, transport=httpx.MockTransport(transport))


def _chat_body(content: Any, usage: Mapping[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}]
    }
    if usage is not None:
        body["usage"] = dict(usage)
    return body


@pytest.mark.asyncio
async def test_chat_request_is_openai_shaped_with_strict_schema() -> None:
    transport = RecordingTransport(body=_chat_body(json.dumps({"answer": "ok"})))
    client = _client(_chat_config(), transport)
    schema = {"type": "object", "required": ["answer"]}
    body = await client.infer(
        "reasoning",
        {"system": "TRUSTED", "user": "UNTRUSTED", "schema": schema},
    )
    assert body["output"] == {"answer": "ok"}
    sent = transport.request_json[0]
    assert sent["model"] == "qwen3-benchmark-gated"
    assert sent["temperature"] == 0
    assert sent["messages"] == [
        {"role": "system", "content": "TRUSTED"},
        {"role": "user", "content": "UNTRUSTED"},
    ]
    assert sent["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "result", "schema": schema, "strict": True},
    }
    # The bearer token is present; no secret is otherwise placed in the body.
    assert transport.requests[0].headers["authorization"] == "Bearer secret-key"
    assert "secret-key" not in transport.requests[0].content.decode()


@pytest.mark.asyncio
async def test_chat_without_schema_omits_response_format() -> None:
    transport = RecordingTransport(body=_chat_body(json.dumps({"answer": "ok"})))
    client = _client(_chat_config(), transport)
    await client.infer("reasoning", {"system": "T", "user": "U", "schema": None})
    assert "response_format" not in transport.request_json[0]


@pytest.mark.asyncio
async def test_chat_usage_is_mapped_to_inference_usage_fields() -> None:
    transport = RecordingTransport(
        body=_chat_body(
            json.dumps({"answer": "ok"}),
            usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        )
    )
    client = _client(_chat_config(), transport)
    body = await client.infer("reasoning", {"system": "T", "user": "U", "schema": None})
    assert body["usage"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


@pytest.mark.asyncio
async def test_non_2xx_becomes_unavailable() -> None:
    transport = RecordingTransport(status=503, body={"error": "down"})
    client = _client(_chat_config(), transport)
    with pytest.raises(InferenceUnavailableError):
        await client.infer("reasoning", {"system": "T", "user": "U", "schema": None})


@pytest.mark.asyncio
async def test_non_json_body_becomes_unavailable() -> None:
    transport = RecordingTransport(raw=b"not json at all")
    client = _client(_chat_config(), transport)
    with pytest.raises(InferenceUnavailableError):
        await client.infer("reasoning", {"system": "T", "user": "U", "schema": None})


@pytest.mark.asyncio
async def test_missing_choices_is_validation_error() -> None:
    transport = RecordingTransport(body={"id": "x", "choices": []})
    client = _client(_chat_config(), transport)
    with pytest.raises(InferenceValidationError):
        await client.infer("reasoning", {"system": "T", "user": "U", "schema": None})


@pytest.mark.asyncio
async def test_content_not_json_is_validation_error() -> None:
    transport = RecordingTransport(body=_chat_body("this is not JSON"))
    client = _client(_chat_config(), transport)
    with pytest.raises(InferenceValidationError):
        await client.infer("reasoning", {"system": "T", "user": "U", "schema": None})


@pytest.mark.asyncio
async def test_content_not_text_is_validation_error() -> None:
    transport = RecordingTransport(body=_chat_body({"already": "parsed"}))
    client = _client(_chat_config(), transport)
    with pytest.raises(InferenceValidationError):
        await client.infer("reasoning", {"system": "T", "user": "U", "schema": None})


@pytest.mark.asyncio
async def test_embedding_request_and_ordered_response() -> None:
    transport = RecordingTransport(
        body={
            "data": [
                {"index": 2, "embedding": [0.5, 0.6]},
                {"index": 0, "embedding": [0.1, 0.2]},
                {"index": 1, "embedding": [0.3, 0.4]},
            ],
            "usage": {"prompt_tokens": 9, "total_tokens": 9},
        }
    )
    client = _client(_embedding_config(), transport)
    body = await client.infer("embedding", {"texts": ["a", "b", "c"]})
    assert body["embeddings"] == [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]]
    assert body["usage"] == {"input_tokens": 9, "total_tokens": 9}
    sent = transport.request_json[0]
    assert sent == {"model": "e5-benchmark-gated", "input": ["a", "b", "c"]}


@pytest.mark.asyncio
async def test_embedding_malformed_row_is_validation_error() -> None:
    transport = RecordingTransport(body={"data": [{"index": 0, "embedding": "nope"}]})
    client = _client(_embedding_config(), transport)
    with pytest.raises(InferenceValidationError):
        await client.infer("embedding", {"texts": ["a"]})


@pytest.mark.asyncio
async def test_vision_gateway_sends_image_url_data_uri() -> None:
    transport = RecordingTransport(body=_chat_body(json.dumps({"summary": "hoá đơn"})))
    config = FPTCapabilityConfig(
        capability="vision",
        endpoint_id="vision-1",
        model_id="vision-benchmark-gated",
        endpoint_url="https://fpt.example/v1/chat/completions",
        api_key="secret-key",
    )
    catalog = FPTCatalog(capabilities={"vision": config})
    gateway = FPTInferenceGateway(
        catalog, FPTClient(catalog, transport=httpx.MockTransport(transport))
    )
    result = await gateway.inspect_vision(
        VisionRequest(
            correlation_id="corr-vision",
            case_id=uuid4(),
            image_base64="QUJD",
            media_type="image/png",
            response_schema={"type": "object", "required": ["summary"]},
        )
    )
    assert result.payload == {"summary": "hoá đơn"}
    user_content = transport.request_json[0]["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert user_content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/png;base64,QUJD"},
    }


@pytest.mark.asyncio
async def test_gateway_still_rejects_forbidden_decision_field() -> None:
    transport = RecordingTransport(
        body=_chat_body(json.dumps({"answer": "ok", "decision": "approve"}))
    )
    config = FPTCapabilityConfig(
        capability="kie",
        endpoint_id="kie-1",
        model_id="kie-benchmark-gated",
        endpoint_url="https://fpt.example/v1/chat/completions",
        api_key="secret-key",
    )
    catalog = FPTCatalog(capabilities={"kie": config})
    gateway = FPTInferenceGateway(
        catalog,
        FPTClient(catalog, transport=httpx.MockTransport(transport)),
        max_attempts=1,
    )
    with pytest.raises(InferenceValidationError):
        await gateway.extract_kie(
            KIERequest(
                correlation_id="corr-kie",
                case_id=uuid4(),
                content="Nội dung",
                document_family="invoice",
                response_schema={"type": "object", "required": ["answer"]},
            )
        )
