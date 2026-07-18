"""Stage 1 domain: prospect, descriptive screening snapshot, contact decision
(master design section 5 stage 1, section 13).

Stage 1 is human-owned.  The models here mirror the ``public.prospects`` /
``public.prospect_screening_snapshots`` / ``public.prospect_contact_decisions``
tables one-for-one and are frozen -- a stored row is an immutable snapshot.

Two invariants the spec calls out are enforced in this module, fail closed:

* NO secret scoring / NO computed verdict.  A screening snapshot is purely
  descriptive; ``ScreeningSnapshot.details`` may not carry a verdict-shaped key
  (``score`` / ``verdict`` / ``approved`` / ``rating`` / ``decision``).  The
  only decision that exists is the human's ``ContactDecisionRecord``.
* The human RM owns the contact decision, and it must carry a rationale --
  ``rationale_vi`` is required and non-blank.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

type ProspectId = UUID
type ProspectScreeningSnapshotId = UUID
type ProspectContactDecisionId = UUID


class ContactDecision(StrEnum):
    """The closed set of human contact decisions for a prospect.

    ``DEFER`` is an explicit "decide later"; there is no implicit / default
    decision -- nothing is ever contacted without a recorded ``CONTACT``.
    """

    CONTACT = "CONTACT"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"
    DEFER = "DEFER"


#: Keys that would smuggle a machine verdict into a descriptive screening
#: snapshot.  Their presence in ``details`` is rejected so no code path can
#: secretly score or pass/fail a prospect (fail closed: the verdict is human).
_FORBIDDEN_DETAIL_KEYS: frozenset[str] = frozenset(
    {"score", "verdict", "approved", "rating", "decision", "pass", "fail"}
)


def assert_details_descriptive(details: Mapping[str, object]) -> None:
    """Raise ``ValueError`` if ``details`` carries a verdict-shaped key.

    Callers use this to fail closed BEFORE persisting a snapshot (the
    ``ScreeningSnapshot`` model applies the same rule as a defence-in-depth
    invariant on any row read back from the store).
    """

    forbidden = _FORBIDDEN_DETAIL_KEYS & {key.lower() for key in details}
    if forbidden:
        raise ValueError(
            "screening details must stay descriptive; it may not carry a "
            f"verdict-shaped key: {sorted(forbidden)}"
        )


class Prospect(BaseModel):
    """A prospect: a stable, immutable identity created by an intake officer.

    Descriptive seed fields are optional; the richer, evolving screening
    picture lives in versioned ``ScreeningSnapshot`` rows (supersession), never
    by mutating this row.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ProspectId
    name_vi: str = Field(min_length=1, max_length=500)
    industry_vi: str | None = Field(default=None, max_length=500)
    years_operating: int | None = Field(default=None, ge=0)
    revenue_band_vi: str | None = Field(default=None, max_length=500)
    legal_status_vi: str | None = Field(default=None, max_length=500)
    notes_vi: str | None = Field(default=None, max_length=4000)
    created_by: UUID
    created_at: datetime

    @model_validator(mode="after")
    def _name_is_not_blank(self) -> Self:
        if not self.name_vi.strip():
            raise ValueError("prospect name must not be blank")
        return self


class ScreeningSnapshot(BaseModel):
    """A descriptive, versioned screening observation for one prospect.

    Every field is descriptive; ``screening_config_version`` labels the
    synthetic configuration the observation was made under.  ``details`` is a
    schema-versioned object of additional descriptive observations -- it may
    NOT carry a verdict-shaped key (no secret scoring).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ProspectScreeningSnapshotId
    prospect_id: ProspectId
    version: int = Field(ge=1)
    screening_config_version: str = Field(min_length=1, max_length=200)
    industry_vi: str | None = Field(default=None, max_length=500)
    years_operating: int | None = Field(default=None, ge=0)
    revenue_band_vi: str | None = Field(default=None, max_length=500)
    legal_status_vi: str | None = Field(default=None, max_length=500)
    credit_history_vi: str | None = Field(default=None, max_length=2000)
    risk_appetite_note_vi: str | None = Field(default=None, max_length=2000)
    details: Mapping[str, object] = Field(default_factory=dict)
    created_by: UUID
    created_at: datetime

    @model_validator(mode="after")
    def _config_version_and_details_are_descriptive(self) -> Self:
        if not self.screening_config_version.strip():
            raise ValueError("screening_config_version must not be blank")
        assert_details_descriptive(self.details)
        return self


class ContactDecisionRecord(BaseModel):
    """A durable record of the human RM's contact decision for a prospect.

    Recording this has NO side effect -- it never triggers any contact.  The
    rationale is the human's own reasoning and is required.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: ProspectContactDecisionId
    prospect_id: ProspectId
    decision: ContactDecision
    rationale_vi: str = Field(min_length=1, max_length=4000)
    decided_by: UUID
    created_at: datetime

    @model_validator(mode="after")
    def _rationale_is_not_blank(self) -> Self:
        if not self.rationale_vi.strip():
            raise ValueError("a contact decision requires a non-blank rationale")
        return self


__all__ = [
    "ContactDecision",
    "ContactDecisionRecord",
    "Prospect",
    "ProspectContactDecisionId",
    "ProspectId",
    "ProspectScreeningSnapshotId",
    "ScreeningSnapshot",
    "assert_details_descriptive",
]
