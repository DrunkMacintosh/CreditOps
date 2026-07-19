"""Provider-neutral contracts for managed inference.

The application depends on this port rather than an SDK.  The only production
implementation currently permitted is the FPT managed adapter.  Keeping the
request and response contracts here makes it impossible for a model response
to become authoritative state without passing through application validation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InferenceError(RuntimeError):
    """Base class for failures calling or validating a provider response."""


class InferenceNotProvisionedError(InferenceError):
    """The capability has NO configured route in this deployment.

    Permanent for the lifetime of the deployment configuration -- unlike a
    transient :class:`InferenceUnavailableError`, retrying can never succeed
    until the deployment itself changes, so stages may make an explicit,
    audited degradation decision instead of retrying forever.
    """


class InferenceUnavailableError(InferenceError):
    """The configured managed endpoint could not be reached or is disabled."""


class InferenceValidationError(InferenceError):
    """A provider response did not satisfy the requested output contract."""


class InferenceCapability(StrEnum):
    REASONING = "reasoning"
    KIE = "kie"
    TABLE = "table"
    VISION = "vision"
    EMBEDDING = "embedding"


class ModelCallContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    correlation_id: str = Field(min_length=1, max_length=128)
    case_id: UUID
    document_version_id: UUID | None = None


class ReasonRequest(ModelCallContext):
    content: str = Field(min_length=1, max_length=200_000)
    response_schema: Mapping[str, Any]
    system_context: str = Field(default="", max_length=30_000)


class KIERequest(ModelCallContext):
    content: str = Field(min_length=1, max_length=200_000)
    document_family: str = Field(min_length=1, max_length=80)
    response_schema: Mapping[str, Any]


class TableRequest(ModelCallContext):
    content: str = Field(min_length=1, max_length=200_000)
    document_family: str = Field(min_length=1, max_length=80)
    response_schema: Mapping[str, Any]


class VisionRequest(ModelCallContext):
    image_base64: str = Field(min_length=1, max_length=20_000_000)
    media_type: str = Field(min_length=1, max_length=100)
    response_schema: Mapping[str, Any]


class EmbeddingRequest(ModelCallContext):
    texts: Sequence[str] = Field(min_length=1, max_length=128)
    expected_dimension: int | None = Field(default=None, ge=1, le=8192)


class InferenceUsage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class InferenceResult(BaseModel):
    """Validated provider output plus the immutable call identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: str
    provider: Literal["FPT"]
    case_id: UUID
    document_version_id: UUID | None = None
    endpoint_id: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    payload: Any
    prompt_version: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    route_version: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1, max_length=128)
    started_at: datetime
    latency_ms: int = Field(ge=0)
    usage: InferenceUsage | None = None
    validation: Literal["passed"] = "passed"


class InferenceGateway(Protocol):
    async def reason(self, request: ReasonRequest) -> InferenceResult: ...

    async def extract_kie(self, request: KIERequest) -> InferenceResult: ...

    async def extract_table(self, request: TableRequest) -> InferenceResult: ...

    async def inspect_vision(self, request: VisionRequest) -> InferenceResult: ...

    async def embed(self, request: EmbeddingRequest) -> InferenceResult: ...
