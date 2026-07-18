from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from creditops.application.ports.model_gateway import (
    InferenceUnavailableError,
    InferenceValidationError,
)
from creditops.infrastructure.fpt.catalog import (
    CapabilityName,
    FPTCapabilityConfig,
    FPTCatalog,
)


class FPTClient:
    """HTTP adapter for one configured FPT AI Factory endpoint.

    FPT AI Factory Serverless Inference is OpenAI-compatible: chat capabilities
    speak ``POST <endpoint_url>`` with a ``/v1/chat/completions`` body and
    embeddings speak ``POST <endpoint_url>`` with a ``/v1/embeddings`` body.  The
    operator configures ``endpoint_url`` to the FULL path for each capability, so
    this class posts to it verbatim and never constructs provider paths.

    The wire protocol is OpenAI, but the seam exposed to the gateway is kept
    semantic and provider-neutral: ``infer`` accepts a normalized payload and
    returns a normalized body (``output``/``embeddings`` plus mapped ``usage``).
    No other provider or model can be selected by this class.
    """

    def __init__(
        self,
        catalog: FPTCatalog,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
        max_response_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if max_response_bytes <= 0 or max_response_bytes > 50 * 1024 * 1024:
            raise ValueError("max_response_bytes is outside the bounded range")
        self.catalog = catalog
        self._max_response_bytes = max_response_bytes
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0)),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def infer(
        self, capability: CapabilityName, payload: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        try:
            config = self.catalog.config_for(capability)
        except ValueError as exc:
            raise InferenceUnavailableError(str(exc)) from exc
        if capability == "embedding":
            request_json = self._embedding_request(config, payload)
        else:
            request_json = self._chat_request(config, payload)
        body = await self._post(config, request_json)
        if capability == "embedding":
            return self._embedding_response(body)
        return self._chat_response(body)

    @staticmethod
    def _chat_request(
        config: FPTCapabilityConfig, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Build an OpenAI ``chat/completions`` request from a semantic payload.

        The trusted system instruction and the untrusted user content are kept in
        distinct message roles; when a JSON schema is supplied the model is
        constrained with strict structured output.
        """

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": payload["system"]},
            {"role": "user", "content": payload["user"]},
        ]
        request: dict[str, Any] = {
            "model": config.model_id,
            "messages": messages,
            "temperature": 0,
        }
        schema = payload.get("schema")
        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "result",
                    "schema": dict(schema),
                    "strict": True,
                },
            }
        return request

    @staticmethod
    def _embedding_request(
        config: FPTCapabilityConfig, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        texts = payload["texts"]
        return {"model": config.model_id, "input": list(texts)}

    async def _post(
        self, config: FPTCapabilityConfig, request_json: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        headers = {
            "Authorization": f"Bearer {config.api_key.get_secret_value()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-FPT-Endpoint-ID": config.endpoint_id,
            "X-FPT-Model-ID": config.model_id,
        }
        try:
            async with self._client.stream(
                "POST",
                config.endpoint_url,
                headers=headers,
                json=request_json,
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise InferenceUnavailableError(
                        f"FPT endpoint returned HTTP {response.status_code}"
                    )
                declared_length = response.headers.get("content-length")
                if declared_length is not None:
                    try:
                        declared_bytes = int(declared_length)
                    except ValueError as exc:
                        raise InferenceUnavailableError("FPT response length was invalid") from exc
                    if declared_bytes > self._max_response_bytes:
                        raise InferenceUnavailableError("FPT response exceeded the byte limit")
                body_bytes = bytearray()
                async for chunk in response.aiter_bytes(64 * 1024):
                    body_bytes.extend(chunk)
                    if len(body_bytes) > self._max_response_bytes:
                        raise InferenceUnavailableError("FPT response exceeded the byte limit")
        except httpx.HTTPError as exc:
            raise InferenceUnavailableError("FPT request failed") from exc
        try:
            body = json.loads(body_bytes)
        except ValueError as exc:
            raise InferenceUnavailableError("FPT response was not JSON") from exc
        if not isinstance(body, Mapping):
            raise InferenceUnavailableError("FPT response must be a JSON object")
        return body

    @staticmethod
    def _chat_response(body: Mapping[str, Any]) -> Mapping[str, Any]:
        """Translate an OpenAI chat response into the normalized gateway body.

        The model's message content is untrusted text; it is parsed as JSON and
        handed back as ``output`` for the gateway to schema-validate.  A missing
        choice or non-JSON content is a validation failure so the gateway's
        repair/retry path handles it rather than treating it as an outage.
        """

        body = _unwrap_fpt_envelope(body)
        choices = body.get("choices")
        if (
            not isinstance(choices, Sequence)
            or isinstance(choices, str | bytes)
            or not choices
        ):
            raise InferenceValidationError("FPT chat response contained no choices")
        first = choices[0]
        if not isinstance(first, Mapping):
            raise InferenceValidationError("FPT chat choice is malformed")
        message = first.get("message")
        if not isinstance(message, Mapping):
            raise InferenceValidationError("FPT chat message is malformed")
        content = message.get("content")
        if not isinstance(content, str):
            raise InferenceValidationError("FPT chat content was not text")
        try:
            output = json.loads(content)
        except ValueError as exc:
            raise InferenceValidationError("FPT chat content was not valid JSON") from exc
        result: dict[str, Any] = {"output": output}
        usage = _map_usage(body.get("usage"))
        if usage is not None:
            result["usage"] = usage
        return result

    @staticmethod
    def _embedding_response(body: Mapping[str, Any]) -> Mapping[str, Any]:
        """Translate an OpenAI embeddings response into the normalized body.

        Rows are returned in ``index`` order so the gateway can align them
        positionally with the requested texts.
        """

        body = _unwrap_fpt_envelope(body)
        data = body.get("data")
        if not isinstance(data, Sequence) or isinstance(data, str | bytes):
            raise InferenceValidationError("FPT embedding response contained no data")
        ordered: list[tuple[int, Any]] = []
        for position, entry in enumerate(data):
            if not isinstance(entry, Mapping):
                raise InferenceValidationError("FPT embedding row is malformed")
            embedding = entry.get("embedding")
            if not isinstance(embedding, list):
                raise InferenceValidationError("FPT embedding row is malformed")
            index = entry.get("index")
            ordered.append((index if isinstance(index, int) else position, embedding))
        ordered.sort(key=lambda item: item[0])
        result: dict[str, Any] = {"embeddings": [embedding for _, embedding in ordered]}
        usage = _map_usage(body.get("usage"))
        if usage is not None:
            result["usage"] = usage
        return result


def _unwrap_fpt_envelope(body: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap the FPT AI Factory response envelope.

    FPT AI Factory (``mkp-api.fptcloud.com``) wraps the OpenAI-shaped payload in
    ``{"code": 200, "message": ..., "data": {choices|data, usage, ...}}``.  A
    standard OpenAI server returns the payload at the top level (and its
    embeddings ``data`` is a list, not a mapping), so accept either real shape.
    A non-200 application ``code`` is a provider outage, not a schema failure.
    """

    data = body.get("data")
    if isinstance(data, Mapping):
        code = body.get("code")
        if isinstance(code, int) and code != 200:
            raise InferenceUnavailableError(
                f"FPT endpoint returned application code {code}"
            )
        return data
    return body


def _map_usage(raw: Any) -> dict[str, Any] | None:
    """Map OpenAI token accounting onto the ``InferenceUsage`` field names."""

    if not isinstance(raw, Mapping):
        return None
    mapped: dict[str, Any] = {}
    prompt_tokens = raw.get("prompt_tokens")
    completion_tokens = raw.get("completion_tokens")
    total_tokens = raw.get("total_tokens")
    if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
        mapped["input_tokens"] = prompt_tokens
    if isinstance(completion_tokens, int) and completion_tokens >= 0:
        mapped["output_tokens"] = completion_tokens
    if isinstance(total_tokens, int) and total_tokens >= 0:
        mapped["total_tokens"] = total_tokens
    return mapped or None
