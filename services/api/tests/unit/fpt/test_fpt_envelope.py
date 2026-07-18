"""The real FPT AI Factory response is wrapped in {code, message, data:{...}}.

Contract observed at https://marketplace.fptcloud.com/vi/models/deepseek-v4-flash:
the OpenAI-shaped ``choices``/``usage`` live under ``data``, not at the top
level. These tests pin that the client unwraps the envelope (and still accepts a
plain top-level OpenAI response, and treats a non-200 application code as an
outage rather than a schema failure).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from creditops.application.ports.model_gateway import InferenceUnavailableError
from creditops.infrastructure.fpt.client import FPTClient


def _wrapped_chat(content: str) -> dict[str, Any]:
    return {
        "code": 200,
        "message": "Chat completion successful",
        "data": {
            "id": "chatcmpl-ef8435055e3341c596d9bc7b212fe7ee",
            "object": "chat.completion",
            "model": "DeepSeek-V4-Flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 13, "completion_tokens": 10, "total_tokens": 23},
        },
    }


def test_fpt_wrapped_chat_envelope_is_unwrapped() -> None:
    result = FPTClient._chat_response(_wrapped_chat(json.dumps({"khach_hang": "An Phat"})))
    assert result["output"] == {"khach_hang": "An Phat"}
    assert result["usage"]["input_tokens"] == 13


def test_plain_openai_chat_without_wrapper_still_parses() -> None:
    body = {
        "choices": [{"message": {"content": json.dumps({"x": 1})}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    assert FPTClient._chat_response(body)["output"] == {"x": 1}


def test_fpt_non_200_application_code_is_unavailable() -> None:
    with pytest.raises(InferenceUnavailableError):
        FPTClient._chat_response(
            {"code": 400, "message": "bad request", "data": {"choices": []}}
        )


def test_fpt_wrapped_embeddings_envelope_is_unwrapped_and_ordered() -> None:
    wrapped = {
        "code": 200,
        "data": {
            "object": "list",
            "data": [
                {"index": 1, "embedding": [0.3, 0.4]},
                {"index": 0, "embedding": [0.1, 0.2]},
            ],
            "usage": {"prompt_tokens": 4, "completion_tokens": 0, "total_tokens": 4},
        },
    }
    result = FPTClient._embedding_response(wrapped)
    assert result["embeddings"] == [[0.1, 0.2], [0.3, 0.4]]
