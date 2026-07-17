# Relationship and Intake Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first working vertical slice of SHB CreditOps EvidenceGraph: a Vietnamese-only Relationship and Intake Agent that ingests a fully invented SME working-capital case, proposes evidence-grounded facts, requires document-by-document confirmation by the assigned intake officer, detects conflicts and gaps, and produces a versioned specialist handoff.

**Architecture:** Use a Next.js browser application backed by a FastAPI service. Persist authoritative state, relational evidence edges, retrieval metadata, and audit events in PostgreSQL; keep storage, OCR, model, and embedding providers behind explicit ports so tests can run locally and the deployed application can fail closed when FPT AI Factory or another required provider is unavailable.

**Tech Stack:** Python 3.12, uv, FastAPI, Pydantic 2, SQLAlchemy 2, Alembic, PostgreSQL with pgvector, pytest, httpx, Next.js, React, TypeScript, pnpm 11, Vitest, Testing Library, and Playwright.

## Global Constraints

- The first product interface and all user-facing intake artifacts are Vietnamese only.
- The Credit Case Digital Twin, not chat history, is the source of truth.
- Every extracted fact must be accepted, corrected, marked absent, or marked unreadable by the assigned intake officer before it becomes authoritative.
- Confirmation is document by document; conflicts surface immediately; gaps remain provisional until `Hoàn tất tải hồ sơ`.
- Only the assigned intake officer may confirm or correct facts.
- No credit approval, rejection, scoring, legal determination, exception waiver, or operational banking mutation.
- No customer-facing document request may be released without explicit human approval.
- Development and evaluation use fully invented documents and identities; there is no preloaded case, seeded answer, or demo-only processing path.
- Uploaded documents and retrieved text are untrusted data and cannot change authority, permissions, prompts, or workflow state.
- The configured FPT model endpoint has no silent public-model fallback.
- Policy/checklist RAG remains inactive until an approved, versioned corpus is configured.
- There is no initial fine-tuning; any future fine-tuning is benchmark-gated and task-specific.
- Do not claim production readiness, regulatory compliance, security certification, official SHB policy status, or SHB approval.
- Use append-only audit events and immutable document versions for all material changes.
- Run each task's focused tests before its commit and run the full verification suite at Tasks 15 and 16.

---

## File and responsibility map

```text
pyproject.toml                         Python workspace, API dependencies, pytest and lint configuration
pnpm-workspace.yaml                   JavaScript workspace declaration
package.json                          Root web scripts and runtime floors
.env.example                          Required non-secret configuration contract
services/api/src/creditops/
  main.py                             FastAPI assembly only
  config.py                           Validated environment configuration
  api/                                HTTP request/response adapters
  domain/                             Pure case, document, fact, gap, handoff, and state-machine rules
  application/                        Use cases and provider ports
  infrastructure/                     SQLAlchemy, object storage, parsers, FPT, embeddings, and retrieval adapters
  prompts/                            Versioned Intake Agent instructions
services/api/alembic/                 PostgreSQL schema migrations
services/api/tests/                   Unit, API, integration, security, and evaluation tests
apps/web/app/                         Next.js routes and layouts
apps/web/components/                  Focused Vietnamese UI components
apps/web/lib/                         Typed API client, session, formatting, and status helpers
apps/web/tests/                       Vitest and Testing Library tests
tests/e2e/                            Playwright end-to-end tests
evaluation/                           Held-out synthetic-case schemas, annotations, runner, and reports
scripts/                              Local verification and conditional live-provider smoke tests
```

The domain layer cannot import FastAPI, SQLAlchemy, HTTP clients, filesystem APIs, or frontend code. Infrastructure adapters implement application ports; API routes call application use cases; the frontend calls only versioned API routes.

### Task 1: Repository toolchain and walking skeleton

**Files:**
- Create: `.gitignore`
- Create: `.env.example`
- Create: `.python-version`
- Create: `.nvmrc`
- Create: `pyproject.toml`
- Create: `package.json`
- Create: `pnpm-workspace.yaml`
- Create: `services/api/src/creditops/__init__.py`
- Create: `services/api/src/creditops/config.py`
- Create: `services/api/src/creditops/main.py`
- Create: `services/api/tests/test_health.py`
- Create: `apps/web/package.json`
- Create: `apps/web/tsconfig.json`
- Create: `apps/web/next.config.ts`
- Create: `apps/web/app/layout.tsx`
- Create: `apps/web/app/page.tsx`
- Create: `apps/web/tests/home.test.tsx`

**Interfaces:**
- Consumes: no application code.
- Produces: `creditops.main.app`, `GET /api/v1/health`, the root Vietnamese Next.js page, and reproducible Python/Node workspaces.

- [ ] **Step 1: Write failing API and UI walking-skeleton tests**

```python
# services/api/tests/test_health.py
from fastapi.testclient import TestClient
from creditops.main import app

def test_health_reports_service_without_claiming_production_readiness() -> None:
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"service": "creditops-api", "status": "ok"}
```

```tsx
// apps/web/tests/home.test.tsx
import { render, screen } from "@testing-library/react";
import Home from "../app/page";

it("presents the Vietnamese intake workspace", () => {
  render(<Home />);
  expect(screen.getByRole("heading", { name: "Tiếp nhận hồ sơ tín dụng" })).toBeVisible();
  expect(screen.getByText(/dữ liệu.*tổng hợp/i)).toBeVisible();
});
```

- [ ] **Step 2: Run the focused tests and verify they fail because the workspaces do not exist**

Run:

```bash
uv run pytest services/api/tests/test_health.py -q
pnpm --dir apps/web test -- --run tests/home.test.tsx
```

Expected: the Python import and web package commands fail before the scaffold is created.

- [ ] **Step 3: Create the minimal locked workspaces and health/UI implementation**

```python
# services/api/src/creditops/main.py
from fastapi import FastAPI

app = FastAPI(title="SHB CreditOps EvidenceGraph", version="0.1.0")

@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"service": "creditops-api", "status": "ok"}
```

```tsx
// apps/web/app/page.tsx
export default function Home() {
  return (
    <main lang="vi">
      <h1>Tiếp nhận hồ sơ tín dụng</h1>
      <p>Toàn bộ dữ liệu trong môi trường phát triển là dữ liệu tổng hợp.</p>
    </main>
  );
}
```

Configure `pyproject.toml` for Python `>=3.12,<3.13`, the `services/api/src` package, pytest, Ruff, and mypy. Configure the root and web `package.json` files for Node `>=24,<25`, pnpm `>=11,<12`, Next.js, React, TypeScript, Vitest, Testing Library, ESLint, and Playwright. Generate `uv.lock` and `pnpm-lock.yaml`; do not hand-edit resolved dependency versions.

- [ ] **Step 4: Run toolchain verification**

Run:

```bash
uv sync --all-groups
pnpm install --frozen-lockfile=false
uv run pytest services/api/tests/test_health.py -q
uv run ruff check services/api
uv run mypy services/api/src
pnpm --dir apps/web test -- --run
pnpm --dir apps/web lint
pnpm --dir apps/web build
```

Expected: one API test and one UI test pass; lint, typecheck, and build exit successfully.

- [ ] **Step 5: Commit the walking skeleton**

```bash
git add .gitignore .env.example .python-version .nvmrc pyproject.toml uv.lock package.json pnpm-workspace.yaml pnpm-lock.yaml services/api apps/web
git commit -m "chore: scaffold intake agent workspaces"
```

### Task 2: Pure domain contracts and deterministic state machine

**Files:**
- Create: `services/api/src/creditops/domain/ids.py`
- Create: `services/api/src/creditops/domain/enums.py`
- Create: `services/api/src/creditops/domain/evidence.py`
- Create: `services/api/src/creditops/domain/cases.py`
- Create: `services/api/src/creditops/domain/transitions.py`
- Create: `services/api/tests/unit/domain/test_transitions.py`
- Create: `services/api/tests/unit/domain/test_evidence.py`

**Interfaces:**
- Consumes: Python standard library and Pydantic only.
- Produces: `CaseState`, `DocumentState`, `FactDisposition`, `CandidateFact`, `FactConfirmation`, `ConfirmedFact`, `transition_document`, and `transition_case`.

- [ ] **Step 1: Write failing state and provenance tests**

```python
from uuid import uuid4
import pytest
from creditops.domain.enums import DocumentState
from creditops.domain.transitions import InvalidTransition, transition_document

def test_document_cannot_be_confirmed_with_open_candidates() -> None:
    with pytest.raises(InvalidTransition, match="chưa được xử lý"):
        transition_document(
            current=DocumentState.UNDER_REVIEW,
            target=DocumentState.CONFIRMED,
            open_candidate_count=1,
        )

def test_confirmed_transition_requires_zero_open_candidates() -> None:
    assert transition_document(
        current=DocumentState.UNDER_REVIEW,
        target=DocumentState.CONFIRMED,
        open_candidate_count=0,
    ) is DocumentState.CONFIRMED
```

```python
from uuid import uuid4
from creditops.domain.evidence import CandidateFact, PageRegion

def test_candidate_fact_requires_addressable_source_region() -> None:
    fact = CandidateFact(
        id=uuid4(), document_version_id=uuid4(), field_key="requested_amount",
        value="5000000000", confidence=0.92,
        source=PageRegion(page=1, x=0.10, y=0.20, width=0.30, height=0.04),
    )
    assert fact.source.page == 1
```

- [ ] **Step 2: Run the domain tests and verify missing-module failures**

Run: `uv run pytest services/api/tests/unit/domain -q`

Expected: collection fails because `creditops.domain` has not been implemented.

- [ ] **Step 3: Implement immutable domain models and transition tables**

```python
# services/api/src/creditops/domain/enums.py
from enum import StrEnum

class DocumentState(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    UNDER_REVIEW = "UNDER_REVIEW"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    SUPERSEDED = "SUPERSEDED"

class FactDisposition(StrEnum):
    ACCEPTED = "ACCEPTED"
    CORRECTED = "CORRECTED"
    ABSENT = "ABSENT"
    UNREADABLE = "UNREADABLE"
```

```python
# services/api/src/creditops/domain/evidence.py
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

class PageRegion(BaseModel):
    model_config = ConfigDict(frozen=True)
    page: int = Field(ge=1)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

class CandidateFact(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    document_version_id: UUID
    field_key: str = Field(min_length=1)
    value: Any
    confidence: float = Field(ge=0, le=1)
    source: PageRegion
```

Implement explicit allowed-transition mappings. `transition_document` must reject confirmation when `open_candidate_count != 0`; case completion must reject any document not in `CONFIRMED`, `FAILED`, or `SUPERSEDED` with failures explicitly acknowledged.

- [ ] **Step 4: Verify domain behavior and static types**

Run:

```bash
uv run pytest services/api/tests/unit/domain -q
uv run ruff check services/api/src/creditops/domain services/api/tests/unit/domain
uv run mypy services/api/src/creditops/domain
```

Expected: all domain tests pass and the domain package has no infrastructure imports.

- [ ] **Step 5: Commit the domain contracts**

```bash
git add services/api/src/creditops/domain services/api/tests/unit/domain
git commit -m "feat: define intake evidence domain"
```

### Task 3: PostgreSQL persistence, relational evidence edges, and audit

**Files:**
- Create: `services/api/alembic.ini`
- Create: `services/api/alembic/env.py`
- Create: `services/api/alembic/versions/0001_intake_foundation.py`
- Create: `services/api/src/creditops/infrastructure/database.py`
- Create: `services/api/src/creditops/infrastructure/tables.py`
- Create: `services/api/src/creditops/application/ports/repositories.py`
- Create: `services/api/src/creditops/infrastructure/repositories.py`
- Create: `services/api/tests/integration/test_repositories.py`
- Create: `services/api/tests/unit/test_audit_repository.py`

**Interfaces:**
- Consumes: Task 2 domain models.
- Produces: `CaseRepository`, `DocumentRepository`, `FactRepository`, `EvidenceEdgeRepository`, `AuditRepository`, and an initial Alembic schema.

- [ ] **Step 1: Write failing repository contract tests**

```python
from uuid import uuid4
from creditops.domain.enums import DocumentState

async def test_audit_events_are_append_only(audit_repository) -> None:
    event_id = uuid4()
    case_id = uuid4()
    await audit_repository.append(
        event_id=event_id, case_id=case_id, actor_id=uuid4(),
        action="CASE_CREATED", input_version=0, payload={"state": "DRAFT"},
    )
    events = await audit_repository.list_for_case(case_id)
    assert [event.id for event in events] == [event_id]
    assert not hasattr(audit_repository, "delete")

async def test_edge_links_exact_source_and_target_versions(evidence_edge_repository) -> None:
    edge = await evidence_edge_repository.add(
        case_id=uuid4(), edge_type="SUPPORTS",
        source_type="PAGE_REGION", source_id=uuid4(), source_version=1,
        target_type="CANDIDATE_FACT", target_id=uuid4(), target_version=1,
    )
    assert edge.source_version == edge.target_version == 1
```

- [ ] **Step 2: Run the repository tests and verify they fail**

Run: `uv run pytest services/api/tests/unit/test_audit_repository.py services/api/tests/integration/test_repositories.py -q`

Expected: collection fails because the ports and adapters are absent.

- [ ] **Step 3: Implement tables, migration, and repository adapters**

Create normalized tables for users, cases, case_versions, financing_requests, documents, document_versions, page_regions, candidate_facts, fact_confirmations, confirmed_facts, conflicts, evidence_gaps, retrieval_hits, evidence_edges, tasks, handoff_artifacts, agent_executions, and audit_events. Use UUID primary keys, UTC timestamps, explicit version columns, foreign keys, and unique constraints for content hashes and idempotency keys.

```python
# services/api/src/creditops/application/ports/repositories.py
from typing import Protocol
from uuid import UUID

class AuditRepository(Protocol):
    async def append(
        self, *, event_id: UUID, case_id: UUID, actor_id: UUID,
        action: str, input_version: int, payload: dict[str, object],
    ) -> None: ...

    async def list_for_case(self, case_id: UUID) -> list[object]: ...
```

The SQL adapter exposes no update or delete method for `audit_events`. Tests use `sqlite+aiosqlite:///:memory:` for fast contracts; PostgreSQL-only migration tests run when `TEST_DATABASE_URL` is set and otherwise report a documented skip.

- [ ] **Step 4: Run repository and migration verification**

Run:

```bash
uv run pytest services/api/tests/unit/test_audit_repository.py -q
uv run pytest services/api/tests/integration/test_repositories.py -q
uv run alembic -c services/api/alembic.ini upgrade head --sql > /tmp/creditops-schema.sql
rg "audit_events|evidence_edges|candidate_facts|confirmed_facts" /tmp/creditops-schema.sql
```

Expected: repository tests pass; generated SQL contains all four required tables.

- [ ] **Step 5: Commit persistence foundation**

```bash
git add services/api/alembic.ini services/api/alembic services/api/src/creditops/application/ports services/api/src/creditops/infrastructure services/api/tests/integration services/api/tests/unit/test_audit_repository.py
git commit -m "feat: persist versioned intake evidence"
```

### Task 4: Authentication and assigned-intake-officer authorization

**Files:**
- Create: `services/api/src/creditops/application/auth.py`
- Create: `services/api/src/creditops/infrastructure/jwt_auth.py`
- Create: `services/api/src/creditops/api/dependencies.py`
- Create: `services/api/src/creditops/api/session.py`
- Create: `services/api/src/creditops/api/cases.py`
- Modify: `services/api/src/creditops/main.py`
- Create: `services/api/tests/api/test_case_authorization.py`
- Create: `services/api/tests/security/test_jwt_validation.py`

**Interfaces:**
- Consumes: repositories from Task 3.
- Produces: `Actor`, `JwtVerifier`, `require_actor`, `require_assigned_officer`, a development-only HttpOnly session bootstrap, `POST /api/v1/cases`, and `GET /api/v1/cases/{case_id}`.

- [ ] **Step 1: Write failing authentication and assignment tests**

```python
def test_create_case_assigns_authenticated_intake_officer(client, officer_token) -> None:
    response = client.post(
        "/api/v1/cases", headers={"Authorization": f"Bearer {officer_token}"},
        json={"customer_reference": "KH-TONG-HOP-001", "requested_amount": "5000000000", "currency": "VND"},
    )
    assert response.status_code == 201
    assert response.json()["assigned_officer_id"] == client.officer_id

def test_other_officer_cannot_open_unassigned_case(client, other_officer_token, case_id) -> None:
    response = client.get(
        f"/api/v1/cases/{case_id}",
        headers={"Authorization": f"Bearer {other_officer_token}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "CASE_ACCESS_DENIED"
```

- [ ] **Step 2: Run the authorization tests and verify failure**

Run: `uv run pytest services/api/tests/api/test_case_authorization.py services/api/tests/security/test_jwt_validation.py -q`

Expected: tests fail because no JWT dependency or case routes exist.

- [ ] **Step 3: Implement strict JWT validation and case-scoped policy**

```python
# services/api/src/creditops/application/auth.py
from dataclasses import dataclass
from uuid import UUID

@dataclass(frozen=True)
class Actor:
    id: UUID
    roles: frozenset[str]

def require_assigned_officer(*, actor: Actor, assigned_officer_id: UUID) -> None:
    if "INTAKE_OFFICER" not in actor.roles or actor.id != assigned_officer_id:
        raise PermissionError("CASE_ACCESS_DENIED")
```

Validate JWT signature, issuer, audience, expiry, subject UUID, and allowed roles from either a bearer token or an HttpOnly `creditops_session` cookie. Permit symmetric signing only when `APP_ENV` is `test` or `development`. A development-only session bootstrap is available only when `APP_ENV=development` and `DEV_AUTH_ENABLED=true`; it accepts the single officer UUID configured in `DEV_INTAKE_OFFICER_ID`, writes a `Secure` cookie whenever HTTPS is enabled, and returns 404 in every other environment. Staging and production startup require a configured JWKS URL, issuer, and audience. Record `CASE_CREATED` and access-denied audit events without logging token contents.

- [ ] **Step 4: Run API, security, and configuration tests**

Run:

```bash
uv run pytest services/api/tests/api/test_case_authorization.py services/api/tests/security/test_jwt_validation.py -q
uv run ruff check services/api/src/creditops/application/auth.py services/api/src/creditops/infrastructure/jwt_auth.py services/api/src/creditops/api
```

Expected: valid assigned-officer access passes; missing, expired, wrong-audience, and unassigned tokens are rejected.

- [ ] **Step 5: Commit identity and assignment enforcement**

```bash
git add services/api/src/creditops/application/auth.py services/api/src/creditops/infrastructure/jwt_auth.py services/api/src/creditops/api services/api/src/creditops/main.py services/api/tests/api services/api/tests/security
git commit -m "feat: enforce assigned intake officer access"
```

### Task 5: Secure immutable document upload and versioning

**Files:**
- Create: `services/api/src/creditops/application/ports/object_store.py`
- Create: `services/api/src/creditops/application/ports/malware_scanner.py`
- Create: `services/api/src/creditops/infrastructure/object_store.py`
- Create: `services/api/src/creditops/infrastructure/clamav_scanner.py`
- Create: `services/api/src/creditops/application/upload_document.py`
- Create: `services/api/src/creditops/api/documents.py`
- Modify: `services/api/src/creditops/main.py`
- Create: `services/api/tests/unit/test_upload_document.py`
- Create: `services/api/tests/api/test_document_upload.py`
- Create: `services/api/tests/security/test_upload_validation.py`

**Interfaces:**
- Consumes: assigned-officer policy, document repositories, audit repository.
- Produces: `ObjectStore.put_immutable`, `MalwareScanner.scan`, `UploadDocument.execute`, and `POST /api/v1/cases/{case_id}/documents`.

- [ ] **Step 1: Write failing upload, duplicate, and unsafe-file tests**

```python
async def test_upload_hashes_and_stores_immutable_original(upload_document, pdf_bytes) -> None:
    result = await upload_document.execute(
        case_id=upload_document.case_id, actor=upload_document.actor,
        filename="de-nghi-cap-tin-dung.pdf", content_type="application/pdf",
        body=pdf_bytes, idempotency_key="upload-001",
    )
    assert result.version == 1
    assert len(result.sha256) == 64
    assert result.state == "UPLOADED"

async def test_exact_duplicate_returns_existing_version(upload_document, pdf_bytes) -> None:
    first = await upload_document.execute(case_id=upload_document.case_id, actor=upload_document.actor, filename="a.pdf", content_type="application/pdf", body=pdf_bytes, idempotency_key="a")
    second = await upload_document.execute(case_id=upload_document.case_id, actor=upload_document.actor, filename="copy.pdf", content_type="application/pdf", body=pdf_bytes, idempotency_key="b")
    assert second.document_version_id == first.document_version_id
    assert second.duplicate is True
```

- [ ] **Step 2: Run upload tests and verify missing-use-case failures**

Run: `uv run pytest services/api/tests/unit/test_upload_document.py services/api/tests/api/test_document_upload.py services/api/tests/security/test_upload_validation.py -q`

Expected: collection fails before the object-store port and upload use case exist.

- [ ] **Step 3: Implement streaming validation, hashing, immutable storage, and idempotency**

```python
# services/api/src/creditops/application/ports/object_store.py
from typing import AsyncIterator, Protocol

class ObjectStore(Protocol):
    async def put_immutable(
        self, *, key: str, chunks: AsyncIterator[bytes],
        content_type: str, sha256: str,
    ) -> None: ...

    async def open(self, *, key: str) -> AsyncIterator[bytes]: ...
```

```python
# services/api/src/creditops/application/ports/malware_scanner.py
from typing import Protocol

class MalwareScanner(Protocol):
    async def scan(self, *, content: bytes, filename: str) -> str: ...
```

Accept PDF, PNG, JPEG, DOCX, and XLSX only after matching detected magic bytes. Enforce configured byte and page limits, reject archives and macro-enabled office files, calculate SHA-256 during streaming, require an idempotency key, and run the configured malware scanner before writing the authoritative original. Provide a ClamAV adapter and a deterministic clean/infected fake only in tests. Non-test startup fails when no scanner is configured. Provide explicit `filesystem` and `s3` object-store adapters selected by configuration; production configuration must reject `filesystem`, require HTTPS, require S3 server-side encryption, and reject public bucket access.

- [ ] **Step 4: Verify upload behavior and security gates**

Run:

```bash
uv run pytest services/api/tests/unit/test_upload_document.py -q
uv run pytest services/api/tests/api/test_document_upload.py -q
uv run pytest services/api/tests/security/test_upload_validation.py -q
```

Expected: valid files create immutable versions; duplicates do not reprocess; spoofed types, oversized files, macros, missing idempotency keys, and unassigned officers are rejected.

- [ ] **Step 5: Commit secure upload**

```bash
git add services/api/src/creditops/application/ports/object_store.py services/api/src/creditops/application/ports/malware_scanner.py services/api/src/creditops/infrastructure/object_store.py services/api/src/creditops/infrastructure/clamav_scanner.py services/api/src/creditops/application/upload_document.py services/api/src/creditops/api/documents.py services/api/src/creditops/main.py services/api/tests/unit/test_upload_document.py services/api/tests/api/test_document_upload.py services/api/tests/security/test_upload_validation.py
git commit -m "feat: add immutable intake document upload"
```

### Task 6: Addressable document parsing and OCR boundary

**Files:**
- Create: `services/api/src/creditops/application/ports/document_parser.py`
- Create: `services/api/src/creditops/infrastructure/parsers/pdf.py`
- Create: `services/api/src/creditops/infrastructure/parsers/images.py`
- Create: `services/api/src/creditops/infrastructure/parsers/office.py`
- Create: `services/api/src/creditops/application/parse_document.py`
- Modify: `services/api/src/creditops/api/documents.py`
- Create: `services/api/tests/unit/parsers/test_pdf_parser.py`
- Create: `services/api/tests/unit/parsers/test_office_parser.py`
- Create: `services/api/tests/unit/test_parse_document.py`
- Create: `services/api/tests/fixtures/documents/README.md`

**Interfaces:**
- Consumes: immutable object store and document repository.
- Produces: `DocumentParser.parse`, `ParsedDocument`, `ParsedPage`, `TextRegion`, `ParseDocument.execute`, `GET /api/v1/documents/{document_id}`, and the authorized `GET /api/v1/documents/{document_id}/pages/{page_number}/preview` endpoint.

- [ ] **Step 1: Write failing parser grounding and unreadable-page tests**

```python
from creditops.application.ports.document_parser import ParsedDocument

async def test_pdf_parser_returns_page_normalized_regions(pdf_parser, text_pdf) -> None:
    parsed = await pdf_parser.parse(filename="request.pdf", body=text_pdf)
    assert isinstance(parsed, ParsedDocument)
    assert parsed.pages[0].number == 1
    assert parsed.pages[0].regions[0].text
    assert 0 <= parsed.pages[0].regions[0].x <= 1

async def test_unreadable_scan_is_not_filled_with_invented_text(parse_document, blank_scan) -> None:
    result = await parse_document.execute(document_version_id=parse_document.document_version_id, body=blank_scan)
    assert result.pages[0].status == "UNREADABLE"
    assert result.pages[0].regions == []
```

- [ ] **Step 2: Run parser tests and verify missing-port failures**

Run: `uv run pytest services/api/tests/unit/parsers services/api/tests/unit/test_parse_document.py -q`

Expected: collection fails because parsing contracts do not exist.

- [ ] **Step 3: Implement safe format-specific parsers and an explicit OCR provider boundary**

```python
# services/api/src/creditops/application/ports/document_parser.py
from dataclasses import dataclass
from typing import Protocol

@dataclass(frozen=True)
class TextRegion:
    text: str
    x: float
    y: float
    width: float
    height: float

@dataclass(frozen=True)
class ParsedPage:
    number: int
    width: int
    height: int
    status: str
    regions: tuple[TextRegion, ...]

@dataclass(frozen=True)
class ParsedDocument:
    pages: tuple[ParsedPage, ...]

class DocumentParser(Protocol):
    async def parse(self, *, filename: str, body: bytes) -> ParsedDocument: ...
```

Use PyMuPDF for PDF text and geometry, Pillow for image validation, `python-docx` for DOCX paragraphs/tables, and `openpyxl` in read-only/data-only mode for XLSX. Scanned pages call a configured OCR adapter; if no OCR provider is configured or the provider produces no grounded regions, mark the page `UNREADABLE`. Do not execute formulas, macros, links, or embedded objects. The metadata and preview endpoints enforce assigned-case access and append `DOCUMENT_METADATA_VIEWED` or `DOCUMENT_PAGE_VIEWED` audit events. Preview rendering returns a safe derived page image with `Content-Disposition: inline`, a restrictive content security policy, and no direct object-store URL.

- [ ] **Step 4: Verify parsing and prompt-injection isolation**

Run:

```bash
uv run pytest services/api/tests/unit/parsers services/api/tests/unit/test_parse_document.py -q
uv run ruff check services/api/src/creditops/infrastructure/parsers services/api/src/creditops/application/parse_document.py
```

Expected: addressable text is preserved; unreadable content remains empty; strings resembling system instructions are returned only as document text.

- [ ] **Step 5: Commit document parsing boundary**

```bash
git add services/api/src/creditops/application/ports/document_parser.py services/api/src/creditops/infrastructure/parsers services/api/src/creditops/application/parse_document.py services/api/src/creditops/api/documents.py services/api/tests/unit/parsers services/api/tests/unit/test_parse_document.py services/api/tests/fixtures/documents/README.md
git commit -m "feat: parse intake documents with provenance"
```

### Task 7: FPT model gateway and structured Intake Agent extraction

**Files:**
- Create: `services/api/src/creditops/application/ports/model_gateway.py`
- Create: `services/api/src/creditops/infrastructure/fpt_model_gateway.py`
- Create: `services/api/src/creditops/domain/document_schemas.py`
- Create: `services/api/src/creditops/prompts/intake/v1.md`
- Create: `services/api/src/creditops/application/extract_candidates.py`
- Create: `services/api/src/creditops/application/process_document.py`
- Modify: `services/api/src/creditops/api/documents.py`
- Create: `services/api/tests/unit/test_extract_candidates.py`
- Create: `services/api/tests/unit/test_document_schemas.py`
- Create: `services/api/tests/integration/test_fpt_gateway_contract.py`
- Create: `services/api/tests/security/test_document_prompt_injection.py`
- Create: `scripts/smoke_fpt_gateway.py`

**Interfaces:**
- Consumes: `ParsedDocument`, document version, model configuration, and repositories.
- Produces: `ModelGateway.structured_completion`, `IntakeExtraction`, `ExtractCandidates.execute`, `ProcessDocument.execute`, `POST /api/v1/documents/{document_id}/process`, and a conditional live FPT smoke test.

- [ ] **Step 1: Write failing structured extraction and no-fallback tests**

```python
async def test_extract_candidates_rejects_fact_without_source_region(extract_candidates, fake_model) -> None:
    fake_model.output = {
        "document_family": "CREDIT_REQUEST",
        "facts": [{"field_key": "requested_amount", "value": "5000000000", "confidence": 0.9}],
    }
    result = await extract_candidates.execute(extract_candidates.command)
    assert result.accepted == []
    assert result.rejected[0].reason == "SOURCE_REQUIRED"

async def test_gateway_does_not_fallback_when_fpt_is_unavailable(fpt_gateway) -> None:
    fpt_gateway.transport.raise_connect_error = True
    with pytest.raises(ModelUnavailable, match="FPT_MODEL_UNAVAILABLE"):
        await fpt_gateway.structured_completion(
            request=fpt_gateway.valid_request,
            schema=IntakeExtraction,
        )
    assert fpt_gateway.transport.request_count == 1
```

- [ ] **Step 2: Run model tests and verify missing-gateway failures**

Run: `uv run pytest services/api/tests/unit/test_extract_candidates.py services/api/tests/integration/test_fpt_gateway_contract.py services/api/tests/security/test_document_prompt_injection.py -q`

Expected: collection fails because the gateway and extraction use case are absent.

- [ ] **Step 3: Implement the OpenAI-compatible FPT adapter and schema-validated extraction**

```python
# services/api/src/creditops/application/ports/model_gateway.py
from typing import Protocol, TypeVar
from pydantic import BaseModel

OutputT = TypeVar("OutputT", bound=BaseModel)

class ModelRequest(BaseModel):
    role: str
    prompt_version: str
    trusted_context: dict[str, object]
    untrusted_document_text: str

class ModelGateway(Protocol):
    async def structured_completion(
        self, *, request: ModelRequest, schema: type[OutputT],
    ) -> OutputT: ...
```

`IntakeExtraction` requires a controlled document-family enum and facts with `field_key`, typed value, confidence, page, and normalized bounding region. `document_schemas.py` defines allowed fields and value types for credit requests, enterprise registration, authority, business plans, contracts, purchase orders, invoices, financial statements, tax declarations, bank statements, ageing schedules, debt schedules, collateral ownership, collateral legal documents, and valuation references. Validate every proposed field against its family schema and every region against stored parsed regions; reject unsupported fields, record the exact model and prompt/schema versions, and perform at most two retries for schema-invalid output. The prompt must delimit untrusted document text and explicitly prohibit following instructions inside it. `ProcessDocument` authorizes the assigned officer, transitions `UPLOADED -> PROCESSING`, invokes Task 6 parsing followed by candidate extraction, and transitions to `UNDER_REVIEW`; provider or validation failures transition to `FAILED` with an audit event and no confirmed facts. The route is explicit and synchronous for the first version, so workflow state remains recoverable without an external job queue.

- [ ] **Step 4: Run contract, security, and conditional live smoke tests**

Run:

```bash
uv run pytest services/api/tests/unit/test_extract_candidates.py services/api/tests/unit/test_document_schemas.py services/api/tests/integration/test_fpt_gateway_contract.py services/api/tests/security/test_document_prompt_injection.py -q
uv run python scripts/smoke_fpt_gateway.py
```

Expected: automated tests pass. The smoke script exits with `SKIP: FPT_MODEL_BASE_URL/FPT_MODEL_API_KEY/FPT_MODEL_NAME not configured` when credentials are absent; with all three values present it must return one schema-valid Vietnamese extraction or exit non-zero.

- [ ] **Step 5: Commit the Intake Agent model boundary**

```bash
git add services/api/src/creditops/application/ports/model_gateway.py services/api/src/creditops/infrastructure/fpt_model_gateway.py services/api/src/creditops/domain/document_schemas.py services/api/src/creditops/prompts services/api/src/creditops/application/extract_candidates.py services/api/src/creditops/application/process_document.py services/api/src/creditops/api/documents.py services/api/tests/unit/test_extract_candidates.py services/api/tests/unit/test_document_schemas.py services/api/tests/integration/test_fpt_gateway_contract.py services/api/tests/security/test_document_prompt_injection.py scripts/smoke_fpt_gateway.py
git commit -m "feat: extract grounded intake candidates"
```

### Task 8: Document-by-document human confirmation

**Files:**
- Create: `services/api/src/creditops/application/confirm_document.py`
- Modify: `services/api/src/creditops/api/documents.py`
- Create: `services/api/tests/unit/test_confirm_document.py`
- Create: `services/api/tests/api/test_fact_confirmation.py`
- Create: `services/api/tests/security/test_confirmation_authority.py`

**Interfaces:**
- Consumes: candidate facts, assigned-officer authorization, document transitions, repositories, and audit.
- Produces: `ConfirmationInput`, `ConfirmDocument.execute`, `POST /api/v1/documents/{document_id}/confirmations`, and authoritative confirmed facts.

- [ ] **Step 1: Write failing all-fields and correction-history tests**

```python
async def test_document_requires_disposition_for_every_candidate(confirm_document) -> None:
    command = confirm_document.command_with_two_candidates(confirmations=[confirm_document.accept_first])
    with pytest.raises(OpenCandidatesError) as error:
        await confirm_document.execute(command)
    assert error.value.open_candidate_ids == [confirm_document.second_candidate_id]

async def test_correction_preserves_candidate_and_human_value(confirm_document) -> None:
    result = await confirm_document.execute(confirm_document.correct_amount_command("4800000000"))
    assert result.confirmed_facts[0].value == "4800000000"
    assert result.confirmed_facts[0].candidate_value == "5000000000"
    assert result.confirmed_facts[0].confirmed_by == confirm_document.assigned_officer.id
```

- [ ] **Step 2: Run confirmation tests and verify failure**

Run: `uv run pytest services/api/tests/unit/test_confirm_document.py services/api/tests/api/test_fact_confirmation.py services/api/tests/security/test_confirmation_authority.py -q`

Expected: tests fail before the confirmation use case and route exist.

- [ ] **Step 3: Implement atomic confirmation with optimistic concurrency**

```python
from pydantic import BaseModel, Field
from uuid import UUID
from creditops.domain.enums import FactDisposition

class ConfirmationInput(BaseModel):
    candidate_id: UUID
    disposition: FactDisposition
    corrected_value: str | int | float | bool | None = None
    rationale: str | None = Field(default=None, max_length=1000)

class ConfirmDocumentCommand(BaseModel):
    document_id: UUID
    expected_document_version: int
    confirmations: list[ConfirmationInput]
```

Require exactly one input for every open candidate, reject duplicate candidate IDs, require `corrected_value` only for `CORRECTED`, preserve candidate values, and write confirmations, confirmed facts, the document state transition, and audit events in one transaction. Reject stale `expected_document_version` with HTTP 409 and reject non-assigned actors with HTTP 403.

- [ ] **Step 4: Verify confirmation authority and atomicity**

Run:

```bash
uv run pytest services/api/tests/unit/test_confirm_document.py -q
uv run pytest services/api/tests/api/test_fact_confirmation.py -q
uv run pytest services/api/tests/security/test_confirmation_authority.py -q
```

Expected: every candidate is dispositioned exactly once; corrections retain lineage; rollback leaves no partial confirmed facts.

- [ ] **Step 5: Commit confirmation workflow**

```bash
git add services/api/src/creditops/application/confirm_document.py services/api/src/creditops/api/documents.py services/api/tests/unit/test_confirm_document.py services/api/tests/api/test_fact_confirmation.py services/api/tests/security/test_confirmation_authority.py
git commit -m "feat: require officer confirmation for intake facts"
```

### Task 9: EvidenceGraph traversal, staleness, and immediate conflicts

**Files:**
- Create: `services/api/src/creditops/domain/conflicts.py`
- Create: `services/api/src/creditops/application/update_evidence_graph.py`
- Create: `services/api/src/creditops/application/supersede_document.py`
- Create: `services/api/src/creditops/api/evidence.py`
- Modify: `services/api/src/creditops/api/documents.py`
- Create: `services/api/tests/unit/test_conflict_detection.py`
- Create: `services/api/tests/integration/test_evidence_graph.py`
- Create: `services/api/tests/api/test_document_supersession.py`

**Interfaces:**
- Consumes: confirmed facts, explicit evidence edges, document versions, and audit.
- Produces: `ConflictDetector`, `UpdateEvidenceGraph.execute`, `SupersedeDocument.execute`, and `GET /api/v1/cases/{case_id}/evidence`.

- [ ] **Step 1: Write failing conflict and invalidation tests**

```python
def test_conflict_detector_links_incompatible_confirmed_values(conflict_detector) -> None:
    conflicts = conflict_detector.detect([
        conflict_detector.fact("enterprise_name", "Công ty TNHH Sao Việt", source="registration"),
        conflict_detector.fact("enterprise_name", "Công ty TNHH Sao Viet", source="credit_request"),
    ])
    assert conflicts[0].field_key == "enterprise_name"
    assert len(conflicts[0].fact_ids) == 2

async def test_new_document_version_marks_dependents_stale(supersede_document) -> None:
    result = await supersede_document.execute(supersede_document.command)
    assert result.old_version_state == "SUPERSEDED"
    assert set(result.stale_types) >= {"CONFIRMED_FACT", "CONFLICT", "EVIDENCE_GAP", "HANDOFF_ARTIFACT"}
```

- [ ] **Step 2: Run graph tests and verify missing-service failures**

Run: `uv run pytest services/api/tests/unit/test_conflict_detection.py services/api/tests/integration/test_evidence_graph.py services/api/tests/api/test_document_supersession.py -q`

Expected: tests fail before conflict and supersession services exist.

- [ ] **Step 3: Implement typed edges, deterministic comparators, and transitive staleness**

Use normalized comparators for Unicode text, Vietnamese dates, currency amounts, registration numbers, tax codes, and identity numbers. Do not use fuzzy conflict thresholds for material identifiers; record both interpretations when normalization cannot decide.

```python
class ConflictDetector:
    MATERIAL_EXACT_FIELDS = frozenset({"tax_code", "registration_number", "identity_number"})

    def detect(self, facts: list[ConfirmedFact]) -> list[Conflict]:
        grouped = group_by_field_key(facts)
        return [conflict for values in grouped.values() if (conflict := compare_confirmed_values(values))]
```

Traverse outgoing `DEPENDS_ON` edges from a superseded document version and mark dependent current outputs stale without deleting them. Insert new conflict and staleness audit events idempotently.

- [ ] **Step 4: Verify graph traversal and immediate-conflict API output**

Run:

```bash
uv run pytest services/api/tests/unit/test_conflict_detection.py -q
uv run pytest services/api/tests/integration/test_evidence_graph.py -q
uv run pytest services/api/tests/api/test_document_supersession.py -q
```

Expected: conflicts retain both sources; a new version marks all linked downstream artifacts stale and preserves history.

- [ ] **Step 5: Commit EvidenceGraph behavior**

```bash
git add services/api/src/creditops/domain/conflicts.py services/api/src/creditops/application/update_evidence_graph.py services/api/src/creditops/application/supersede_document.py services/api/src/creditops/api/evidence.py services/api/src/creditops/api/documents.py services/api/tests/unit/test_conflict_detection.py services/api/tests/integration/test_evidence_graph.py services/api/tests/api/test_document_supersession.py
git commit -m "feat: detect and trace intake evidence conflicts"
```

### Task 10: Dual RAG with strict case and policy boundaries

**Files:**
- Create: `services/api/src/creditops/application/ports/embeddings.py`
- Create: `services/api/src/creditops/application/ports/retrieval.py`
- Create: `services/api/src/creditops/infrastructure/embedding_gateway.py`
- Create: `services/api/src/creditops/infrastructure/postgres_retrieval.py`
- Create: `services/api/src/creditops/application/retrieve_evidence.py`
- Create: `services/api/src/creditops/api/retrieval.py`
- Modify: `services/api/alembic/versions/0001_intake_foundation.py`
- Create: `services/api/tests/unit/test_retrieve_evidence.py`
- Create: `services/api/tests/integration/test_retrieval_isolation.py`
- Create: `services/api/tests/security/test_policy_retrieval_fail_closed.py`

**Interfaces:**
- Consumes: confirmed case facts, parsed regions, actor/case authorization, and optional policy corpus configuration.
- Produces: `EmbeddingGateway.embed`, `RetrievalRepository.search_case`, `RetrievalRepository.search_policy`, and `POST /api/v1/cases/{case_id}/retrieval`.

- [ ] **Step 1: Write failing isolation, provenance, and policy-abstention tests**

```python
async def test_case_retrieval_never_returns_another_case(retrieve_evidence) -> None:
    hits = await retrieve_evidence.case_query(
        actor=retrieve_evidence.officer, case_id=retrieve_evidence.case_a,
        question="Nguồn trả nợ là gì?",
    )
    assert hits
    assert {hit.case_id for hit in hits} == {retrieve_evidence.case_a}
    assert all(hit.document_version_id and hit.page and hit.region for hit in hits)

async def test_policy_query_abstains_when_corpus_is_inactive(retrieve_evidence) -> None:
    result = await retrieve_evidence.policy_query(case_id=retrieve_evidence.case_a, question="Hồ sơ còn thiếu gì?")
    assert result.status == "POLICY_CORPUS_UNAVAILABLE"
    assert result.hits == []
```

- [ ] **Step 2: Run retrieval tests and verify missing-adapter failures**

Run: `uv run pytest services/api/tests/unit/test_retrieve_evidence.py services/api/tests/integration/test_retrieval_isolation.py services/api/tests/security/test_policy_retrieval_fail_closed.py -q`

Expected: collection fails before retrieval ports and adapters exist.

- [ ] **Step 3: Implement hybrid retrieval and auditable hit records**

```python
from dataclasses import dataclass
from uuid import UUID

@dataclass(frozen=True)
class RetrievalHit:
    case_id: UUID | None
    corpus_id: UUID | None
    document_version_id: UUID
    page: int
    region_id: UUID
    passage: str
    lexical_score: float
    vector_score: float
    rerank_score: float | None
```

Require case ID and actor authorization for case search. Combine PostgreSQL full-text search with pgvector cosine distance, apply metadata filters before ranking, and persist the query plus returned hit identifiers. Policy search additionally requires `POLICY_RAG_ENABLED=true`, an approved corpus state, applicable effective dates, and actor access. The embedding adapter must fail visibly if its configured endpoint is unavailable; tests use a deterministic fake vectorizer selected only in `APP_ENV=test`.

- [ ] **Step 4: Verify retrieval isolation and PostgreSQL migration**

Run:

```bash
uv run pytest services/api/tests/unit/test_retrieve_evidence.py -q
uv run pytest services/api/tests/integration/test_retrieval_isolation.py -q
uv run pytest services/api/tests/security/test_policy_retrieval_fail_closed.py -q
uv run alembic -c services/api/alembic.ini upgrade head --sql | rg "vector|retrieval_hits"
```

Expected: every case hit is case-scoped and addressable; inactive policy RAG abstains; migration contains vector and retrieval metadata support.

- [ ] **Step 5: Commit bounded dual RAG**

```bash
git add services/api/src/creditops/application/ports/embeddings.py services/api/src/creditops/application/ports/retrieval.py services/api/src/creditops/infrastructure/embedding_gateway.py services/api/src/creditops/infrastructure/postgres_retrieval.py services/api/src/creditops/application/retrieve_evidence.py services/api/src/creditops/api/retrieval.py services/api/alembic/versions/0001_intake_foundation.py services/api/tests/unit/test_retrieve_evidence.py services/api/tests/integration/test_retrieval_isolation.py services/api/tests/security/test_policy_retrieval_fail_closed.py
git commit -m "feat: add case scoped evidence retrieval"
```

### Task 11: Progressive evidence gaps, upload completion, and versioned handoff

**Files:**
- Create: `services/api/src/creditops/domain/gaps.py`
- Create: `services/api/src/creditops/application/update_gaps.py`
- Create: `services/api/src/creditops/application/complete_upload.py`
- Create: `services/api/src/creditops/application/build_handoff.py`
- Create: `services/api/src/creditops/api/gaps.py`
- Create: `services/api/src/creditops/api/handoffs.py`
- Modify: `services/api/src/creditops/main.py`
- Create: `services/api/tests/unit/test_gap_lifecycle.py`
- Create: `services/api/tests/api/test_complete_upload.py`
- Create: `services/api/tests/integration/test_handoff_versioning.py`

**Interfaces:**
- Consumes: confirmed facts, document states, conflicts, retrieval status, evidence edges, and audit.
- Produces: `EvidenceGap`, `UpdateGaps.execute`, `CompleteUpload.execute`, `BuildHandoff.execute`, formal gap reports, and immutable handoff artifacts.

- [ ] **Step 1: Write failing provisional/final and handoff-version tests**

```python
async def test_gaps_remain_provisional_before_upload_completion(update_gaps) -> None:
    gaps = await update_gaps.execute(case_id=update_gaps.case_id, finalize=False)
    assert gaps
    assert {gap.status for gap in gaps} == {"PROVISIONAL"}

async def test_complete_upload_rejects_unconfirmed_document(complete_upload) -> None:
    complete_upload.document_states = ["CONFIRMED", "UNDER_REVIEW"]
    with pytest.raises(CaseNotReady, match="DOCUMENT_REVIEW_INCOMPLETE"):
        await complete_upload.execute(complete_upload.command)

async def test_handoff_binds_to_exact_case_version(build_handoff) -> None:
    artifact = await build_handoff.execute(case_id=build_handoff.case_id, case_version=7)
    assert artifact.case_version == 7
    assert artifact.state == "READY_FOR_SPECIALIST_REVIEW"
    assert artifact.credit_decision is None
```

- [ ] **Step 2: Run gap and handoff tests and verify failure**

Run: `uv run pytest services/api/tests/unit/test_gap_lifecycle.py services/api/tests/api/test_complete_upload.py services/api/tests/integration/test_handoff_versioning.py -q`

Expected: collection fails before the gap and handoff use cases exist.

- [ ] **Step 3: Implement neutral gap records and atomic completion**

```python
from enum import StrEnum
from pydantic import BaseModel
from uuid import UUID

class GapStatus(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    FORMAL = "FORMAL"
    RESOLVED = "RESOLVED"
    STALE = "STALE"

class EvidenceGap(BaseModel):
    id: UUID
    case_id: UUID
    status: GapStatus
    issue_vi: str
    existing_evidence_ids: list[UUID]
    missing_information_vi: str
    affected_task_ids: list[UUID]
    suggested_evidence_vi: list[str]
    policy_citation_ids: list[UUID]
```

Generate only neutral evidence-quality statements; never infer misconduct. `CompleteUpload` requires the assigned officer, optimistic case version, disposition of every candidate, and explicit acknowledgement of failed/unreadable documents. In one transaction, freeze the input version, finalize gaps, build the handoff, transition the case, and append audit events. Store suggested customer requests as drafts with `approval_status=NOT_SUBMITTED`.

- [ ] **Step 4: Verify completion and immutable handoff behavior**

Run:

```bash
uv run pytest services/api/tests/unit/test_gap_lifecycle.py -q
uv run pytest services/api/tests/api/test_complete_upload.py -q
uv run pytest services/api/tests/integration/test_handoff_versioning.py -q
```

Expected: incomplete review blocks finalization; formal gaps retain provenance; a handoff contains no credit outcome and binds to one case version.

- [ ] **Step 5: Commit gap resolution and handoff**

```bash
git add services/api/src/creditops/domain/gaps.py services/api/src/creditops/application/update_gaps.py services/api/src/creditops/application/complete_upload.py services/api/src/creditops/application/build_handoff.py services/api/src/creditops/api/gaps.py services/api/src/creditops/api/handoffs.py services/api/src/creditops/main.py services/api/tests/unit/test_gap_lifecycle.py services/api/tests/api/test_complete_upload.py services/api/tests/integration/test_handoff_versioning.py
git commit -m "feat: finalize intake gaps and handoff"
```

### Task 12: Vietnamese case list, creation, and upload interface

**Files:**
- Create: `apps/web/lib/api/types.ts`
- Create: `apps/web/lib/api/client.ts`
- Create: `apps/web/lib/auth/session.ts`
- Create: `apps/web/lib/format.ts`
- Create: `apps/web/components/synthetic-data-notice.tsx`
- Create: `apps/web/components/cases/case-list.tsx`
- Create: `apps/web/components/cases/create-case-form.tsx`
- Create: `apps/web/components/documents/upload-zone.tsx`
- Create: `apps/web/app/ho-so/page.tsx`
- Create: `apps/web/app/ho-so/tao-moi/page.tsx`
- Create: `apps/web/app/ho-so/[caseId]/tiep-nhan/page.tsx`
- Create: `apps/web/tests/case-list.test.tsx`
- Create: `apps/web/tests/create-case.test.tsx`
- Create: `apps/web/tests/upload-zone.test.tsx`

**Interfaces:**
- Consumes: case and upload API routes from Tasks 4 and 5.
- Produces: typed `apiClient`, Vietnamese case routes, case creation, document inventory, and upload progress.

- [ ] **Step 1: Write failing Vietnamese workflow tests**

```tsx
it("creates a case and opens the intake workspace", async () => {
  const user = userEvent.setup();
  render(<CreateCaseForm onCreated={onCreated} />);
  await user.type(screen.getByLabelText("Mã tham chiếu khách hàng"), "KH-TONG-HOP-001");
  await user.type(screen.getByLabelText("Số tiền đề nghị"), "5000000000");
  await user.click(screen.getByRole("button", { name: "Tạo hồ sơ" }));
  expect(onCreated).toHaveBeenCalledWith(expect.objectContaining({ assignedOfficerId: expect.any(String) }));
});

it("shows a duplicate without uploading it twice", async () => {
  render(<UploadZone caseId="case-1" />);
  await uploadFile("de-nghi.pdf", PDF_BYTES);
  await uploadFile("ban-sao.pdf", PDF_BYTES);
  expect(await screen.findByText("Tài liệu trùng khớp hoàn toàn")).toBeVisible();
});
```

- [ ] **Step 2: Run frontend tests and verify missing-component failures**

Run: `pnpm --dir apps/web test -- --run tests/case-list.test.tsx tests/create-case.test.tsx tests/upload-zone.test.tsx`

Expected: imports fail before the pages and components exist.

- [ ] **Step 3: Implement typed API calls and accessible Vietnamese screens**

```ts
// apps/web/lib/api/client.ts
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${process.env.NEXT_PUBLIC_API_BASE_URL}${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  const body = await response.json();
  if (!response.ok) throw new ApiError(response.status, body.code, body.message_vi);
  return body as T;
}
```

Use semantic HTML, keyboard navigation, visible focus, Vietnamese labels, `aria-live` upload progress, and server-provided error codes mapped to Vietnamese copy. Configure a same-origin `/backend` rewrite in `next.config.ts`, use the HttpOnly session cookie from Task 4, and never store access tokens in browser storage. The synthetic-data notice is persistent but does not alter processing logic. Uploads use `FormData`, send an idempotency key, invoke document processing after a successful upload, and display security, duplicate, processing, review, failed, and confirmed states.

- [ ] **Step 4: Verify frontend behavior, types, and production build**

Run:

```bash
pnpm --dir apps/web test -- --run tests/case-list.test.tsx tests/create-case.test.tsx tests/upload-zone.test.tsx
pnpm --dir apps/web lint
pnpm --dir apps/web build
```

Expected: all tests pass; TypeScript and Next.js builds succeed without English user-facing status strings.

- [ ] **Step 5: Commit case intake UI**

```bash
git add apps/web/lib apps/web/components/synthetic-data-notice.tsx apps/web/components/cases apps/web/components/documents/upload-zone.tsx apps/web/app/ho-so apps/web/tests/case-list.test.tsx apps/web/tests/create-case.test.tsx apps/web/tests/upload-zone.test.tsx
git commit -m "feat: add Vietnamese case intake workspace"
```

### Task 13: Source-grounded document confirmation interface

**Files:**
- Create: `apps/web/components/documents/document-viewer.tsx`
- Create: `apps/web/components/documents/fact-review-form.tsx`
- Create: `apps/web/components/documents/document-review.tsx`
- Create: `apps/web/app/ho-so/[caseId]/tai-lieu/[documentId]/page.tsx`
- Create: `apps/web/tests/document-viewer.test.tsx`
- Create: `apps/web/tests/fact-review-form.test.tsx`
- Create: `apps/web/tests/document-review.test.tsx`

**Interfaces:**
- Consumes: document, page-region, candidate, and confirmation APIs from Tasks 6–8.
- Produces: split document review, source-region highlighting, required disposition for every field, and stale-version conflict recovery.

- [ ] **Step 1: Write failing source highlight and all-fields-required tests**

```tsx
it("highlights the exact source region when a candidate receives focus", async () => {
  const user = userEvent.setup();
  render(<DocumentReview document={documentWithTwoCandidates} />);
  await user.click(screen.getByLabelText("Số tiền đề nghị"));
  expect(screen.getByTestId("source-region-requested-amount")).toHaveAttribute("data-active", "true");
});

it("blocks confirmation until every candidate has a disposition", async () => {
  const user = userEvent.setup();
  render(<FactReviewForm candidates={twoCandidates} onSubmit={onSubmit} />);
  await user.click(screen.getByRole("button", { name: "Xác nhận tài liệu" }));
  expect(screen.getByText("Cần xử lý tất cả trường dữ liệu")).toBeVisible();
  expect(onSubmit).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run document-review tests and verify failure**

Run: `pnpm --dir apps/web test -- --run tests/document-viewer.test.tsx tests/fact-review-form.test.tsx tests/document-review.test.tsx`

Expected: imports fail before review components exist.

- [ ] **Step 3: Implement split review with normalized overlays and four dispositions**

```ts
export type FactDisposition = "ACCEPTED" | "CORRECTED" | "ABSENT" | "UNREADABLE";

export type ConfirmationInput = {
  candidateId: string;
  disposition: FactDisposition;
  correctedValue?: string;
  rationale?: string;
};

export function regionStyle(region: PageRegion): React.CSSProperties {
  return {
    left: `${region.x * 100}%`, top: `${region.y * 100}%`,
    width: `${region.width * 100}%`, height: `${region.height * 100}%`,
  };
}
```

Render the original page or safe derived preview on the left and candidate controls on the right. Require the four explicit dispositions; require a corrected value for `CORRECTED`; show the model candidate read-only; submit the expected document version; on HTTP 409 reload and explain that a newer version exists.

- [ ] **Step 4: Verify accessibility and confirmation behavior**

Run:

```bash
pnpm --dir apps/web test -- --run tests/document-viewer.test.tsx tests/fact-review-form.test.tsx tests/document-review.test.tsx
pnpm --dir apps/web lint
pnpm --dir apps/web build
```

Expected: keyboard focus activates the correct source region; no incomplete form submits; corrections display both values.

- [ ] **Step 5: Commit source-grounded confirmation UI**

```bash
git add apps/web/components/documents apps/web/app/ho-so apps/web/tests/document-viewer.test.tsx apps/web/tests/fact-review-form.test.tsx apps/web/tests/document-review.test.tsx
git commit -m "feat: add document fact confirmation UI"
```

### Task 14: Evidence, gaps, handoff, and audit dashboard

**Files:**
- Create: `apps/web/components/evidence/evidence-map.tsx`
- Create: `apps/web/components/evidence/conflict-list.tsx`
- Create: `apps/web/components/gaps/gap-list.tsx`
- Create: `apps/web/components/handoff/handoff-summary.tsx`
- Create: `apps/web/components/audit/audit-timeline.tsx`
- Create: `apps/web/app/ho-so/[caseId]/doi-chieu/page.tsx`
- Create: `apps/web/app/ho-so/[caseId]/khoang-trong/page.tsx`
- Create: `apps/web/app/ho-so/[caseId]/ban-giao/page.tsx`
- Create: `apps/web/app/ho-so/[caseId]/nhat-ky/page.tsx`
- Create: `apps/web/tests/case-review-dashboard.test.tsx`
- Create: `apps/web/tests/complete-upload.test.tsx`

**Interfaces:**
- Consumes: evidence, conflicts, gaps, completion, handoff, execution, retrieval, and audit APIs.
- Produces: the case-level Vietnamese review workspace and explicit `Hoàn tất tải hồ sơ` gate.

- [ ] **Step 1: Write failing conflict, finalization, and audit tests**

```tsx
it("distinguishes conflict, missing, unreadable, and stale evidence", () => {
  render(<CaseReviewDashboard data={caseWithAllIssueTypes} />);
  expect(screen.getByText("Mâu thuẫn")).toBeVisible();
  expect(screen.getByText("Thiếu bằng chứng")).toBeVisible();
  expect(screen.getByText("Không đọc được")).toBeVisible();
  expect(screen.getByText("Cần phân tích lại")).toBeVisible();
});

it("shows formal gaps only after explicit upload completion", async () => {
  const user = userEvent.setup();
  render(<CompleteUpload caseSummary={readyCase} />);
  expect(screen.getByText("Khoảng trống tạm thời")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Hoàn tất tải hồ sơ" }));
  expect(await screen.findByText("Báo cáo khoảng trống chính thức")).toBeVisible();
});
```

- [ ] **Step 2: Run dashboard tests and verify missing-component failures**

Run: `pnpm --dir apps/web test -- --run tests/case-review-dashboard.test.tsx tests/complete-upload.test.tsx`

Expected: imports fail before dashboard components exist.

- [ ] **Step 3: Implement evidence-first dashboard and immutable handoff views**

Evidence map nodes link to the exact document version and page region. Conflict rows show both confirmed values and sources without choosing a winner. Gap cards show provisional/formal status, existing evidence, missing information, affected work, suggested evidence, and policy citation availability. The handoff view displays the bound case version and explicitly states `Không phải quyết định tín dụng`. Audit entries show actor, role, action, artifact version, timestamp, and outcome while excluding secrets and raw tokens.

```tsx
export function HandoffBoundary() {
  return (
    <aside role="note" aria-label="Giới hạn bàn giao">
      <strong>Không phải quyết định tín dụng</strong>
      <p>Hồ sơ này chỉ sẵn sàng để chuyên gia tiếp tục xem xét.</p>
    </aside>
  );
}
```

- [ ] **Step 4: Verify dashboard rendering and production build**

Run:

```bash
pnpm --dir apps/web test -- --run tests/case-review-dashboard.test.tsx tests/complete-upload.test.tsx
pnpm --dir apps/web lint
pnpm --dir apps/web build
```

Expected: issue types are distinguishable, completion is explicit, source links work, and handoff never displays approval/rejection language.

- [ ] **Step 5: Commit review dashboard**

```bash
git add apps/web/components/evidence apps/web/components/gaps apps/web/components/handoff apps/web/components/audit apps/web/app/ho-so apps/web/tests/case-review-dashboard.test.tsx apps/web/tests/complete-upload.test.tsx
git commit -m "feat: add intake evidence review dashboard"
```

### Task 15: Evaluation harness and generic single-agent baseline

**Files:**
- Create: `evaluation/schema/case-manifest.schema.json`
- Create: `evaluation/schema/ground-truth.schema.json`
- Create: `evaluation/runner.py`
- Create: `evaluation/scoring.py`
- Create: `evaluation/baseline.py`
- Create: `evaluation/report.py`
- Create: `evaluation/generate_fixtures.py`
- Create: `evaluation/README.md`
- Create: `services/api/tests/evaluation/test_scoring.py`
- Create: `services/api/tests/evaluation/test_blind_case_runner.py`
- Create: `scripts/run_evaluation.sh`

**Interfaces:**
- Consumes: normal upload, extraction, confirmation, retrieval, gap, and handoff APIs plus externally supplied fully invented case files and private annotations.
- Produces: deterministic JSON/Markdown reports for the Intake Agent and generic single-agent baseline.

- [ ] **Step 1: Write failing scoring and held-out isolation tests**

```python
from evaluation.scoring import score_facts

def test_fact_scoring_separates_value_and_grounding_accuracy() -> None:
    report = score_facts(
        expected=[{"field_key": "tax_code", "value": "0101234567", "page": 1}],
        actual=[{"field_key": "tax_code", "value": "0101234567", "page": 2}],
    )
    assert report.value_f1 == 1.0
    assert report.grounding_accuracy == 0.0

def test_runner_rejects_case_manifest_containing_ground_truth_path() -> None:
    with pytest.raises(ValueError, match="GROUND_TRUTH_MUST_BE_SEPARATE"):
        load_case_manifest({"documents": [], "ground_truth": "answers.json"})
```

- [ ] **Step 2: Run evaluation tests and verify failure**

Run: `uv run pytest services/api/tests/evaluation -q`

Expected: imports fail before the evaluation package exists.

- [ ] **Step 3: Implement repeatable scoring and baseline isolation**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class FactScore:
    precision: float
    recall: float
    value_f1: float
    grounding_accuracy: float
    unsupported_fact_rate: float
```

The runner uploads cases through normal API routes and never reads annotations until processing is complete. Score document-family accuracy, fact precision/recall, value F1, page/region grounding, schema-valid rate, unsupported-fact rate, conflict recall, gap recall, confirmation burden, gate violations, completion time, latency, and model calls. The baseline uses one generic prompt and the same model gateway but receives no agent-specific tools, state machine authority, or hidden ground truth. It cannot write authoritative case state. `generate_fixtures.py` deterministically creates a small, fully invented Vietnamese PDF and separate annotations for automated E2E tests; generated binaries remain ignored and are never shipped as a preloaded product case.

- [ ] **Step 4: Verify scoring and document the blind-run contract**

Run:

```bash
uv run pytest services/api/tests/evaluation -q
uv run python evaluation/generate_fixtures.py --output evaluation/fixtures/e2e
bash scripts/run_evaluation.sh --help
```

Expected: scoring tests pass; the generator writes `evaluation/fixtures/e2e/de-nghi-cap-tin-dung.pdf` and a separate annotations file; help output requires separate `--case-dir`, `--annotations`, `--system intake|baseline`, and `--output` arguments.

- [ ] **Step 5: Commit evaluation harness**

```bash
git add evaluation services/api/tests/evaluation scripts/run_evaluation.sh
git commit -m "test: add blind intake evaluation harness"
```

### Task 16: End-to-end verification, observability, and release boundary

**Files:**
- Create: `services/api/src/creditops/observability.py`
- Create: `services/api/src/creditops/application/retention.py`
- Create: `services/api/src/creditops/api/errors.py`
- Create: `services/api/tests/security/test_log_redaction.py`
- Create: `services/api/tests/security/test_idempotency.py`
- Create: `services/api/tests/unit/test_retention_policy.py`
- Create: `tests/e2e/intake-flow.spec.ts`
- Create: `scripts/verify.sh`
- Create: `docs/DEVELOPMENT.md`
- Create: `docs/DEPLOYMENT_BOUNDARIES.md`
- Modify: `.env.example`
- Modify: `docs/OPEN_QUESTIONS.md`
- Modify: `docs/DECISION_LOG.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: structured/redacted logs, consistent Vietnamese-safe errors, complete end-to-end verification, developer startup instructions, and explicit deployment blockers.

- [ ] **Step 1: Write failing security and end-to-end tests**

```python
def test_logs_redact_tokens_and_document_text(caplog, api_client, officer_token) -> None:
    api_client.post("/api/v1/cases", headers={"Authorization": f"Bearer {officer_token}"}, json=api_client.valid_case)
    combined = "\n".join(record.message for record in caplog.records)
    assert officer_token not in combined
    assert api_client.valid_case["customer_reference"] not in combined

def test_replayed_completion_key_returns_same_handoff(api_client, ready_case, officer_token) -> None:
    headers = {"Authorization": f"Bearer {officer_token}", "Idempotency-Key": "complete-001"}
    first = api_client.post(f"/api/v1/cases/{ready_case}/complete-upload", headers=headers)
    second = api_client.post(f"/api/v1/cases/{ready_case}/complete-upload", headers=headers)
    assert first.json()["handoff_id"] == second.json()["handoff_id"]
```

```ts
// tests/e2e/intake-flow.spec.ts
test("assigned officer completes a grounded Vietnamese intake", async ({ page }) => {
  await page.goto("/ho-so/tao-moi");
  await page.getByLabel("Mã tham chiếu khách hàng").fill("KH-TONG-HOP-E2E");
  await page.getByRole("button", { name: "Tạo hồ sơ" }).click();
  await page.getByLabel("Tải tài liệu").setInputFiles("evaluation/fixtures/e2e/de-nghi-cap-tin-dung.pdf");
  await page.getByRole("link", { name: "Xác nhận tài liệu" }).click();
  for (const button of await page.getByRole("button", { name: "Chấp nhận", exact: true }).all()) {
    await button.click();
  }
  await page.getByRole("button", { name: "Xác nhận tài liệu" }).click();
  await page.getByRole("button", { name: "Hoàn tất tải hồ sơ" }).click();
  await expect(page.getByText("Sẵn sàng bàn giao thẩm định")).toBeVisible();
  await expect(page.getByText("Không phải quyết định tín dụng")).toBeVisible();
});
```

- [ ] **Step 2: Run focused release-boundary tests and verify failure**

Run:

```bash
uv run pytest services/api/tests/security/test_log_redaction.py services/api/tests/security/test_idempotency.py services/api/tests/unit/test_retention_policy.py -q
pnpm exec playwright test tests/e2e/intake-flow.spec.ts
```

Expected: tests fail before observability, uniform idempotency, and final E2E wiring exist.

- [ ] **Step 3: Implement redacted observability, error mapping, verification script, and honest deployment documentation**

```python
SENSITIVE_LOG_KEYS = frozenset({"authorization", "cookie", "document_text", "raw_value", "api_key", "token"})

def redact(payload: dict[str, object]) -> dict[str, object]:
    return {key: "[REDACTED]" if key.lower() in SENSITIVE_LOG_KEYS else value for key, value in payload.items()}
```

Emit request, use-case, provider, state-transition, and audit correlation IDs without raw document content or secrets. Map domain errors to stable machine codes and Vietnamese messages. Implement a retention-policy evaluator that reports which artifact classes would be eligible under configured durations, but keep destructive execution disabled until an approved policy and authorization mechanism exist; originals, approvals, and audit events must never be silently deleted. `scripts/verify.sh` must run backend tests/lint/types, frontend tests/lint/build, migration SQL checks, and Playwright. `docs/DEPLOYMENT_BOUNDARIES.md` must list unresolved FPT provisioning, durable storage, backup, OIDC, retention, monitoring, model/OCR/embedding benchmarks, policy corpus, and security-review gates; it must explicitly state that passing local tests does not establish production readiness.

Update `docs/OPEN_QUESTIONS.md` only with newly sharpened unresolved items; add a `docs/DECISION_LOG.md` entry only for implementation choices actually validated during execution.

- [ ] **Step 4: Run the complete verification suite**

Run:

```bash
bash scripts/verify.sh
git diff --check
git status --short
```

Expected: all unit, API, integration-contract, security, frontend, and E2E tests pass; conditional PostgreSQL and live FPT checks either pass with configured services or report explicit skips; no whitespace errors; only intentional files remain modified.

- [ ] **Step 5: Commit verified Intake Agent vertical slice**

```bash
git add services/api apps/web tests scripts docs/DEVELOPMENT.md docs/DEPLOYMENT_BOUNDARIES.md docs/OPEN_QUESTIONS.md docs/DECISION_LOG.md .env.example
git commit -m "feat: complete intake agent vertical slice"
```

---

## Final acceptance review

After Task 16, verify these outcomes against the approved design:

- A user can create a case and upload an unseen, fully invented Vietnamese document pack through the normal interface.
- Immutable versions, hashes, processing states, page regions, candidate facts, confirmations, confirmed facts, conflicts, gaps, retrieval hits, handoff artifacts, executions, and audit events persist through explicit contracts.
- Only the assigned intake officer can confirm or correct facts.
- Every candidate is dispositioned document by document and every confirmed material fact has an addressable source.
- Conflicts appear before completion; formal gaps and handoff appear only after explicit completion.
- Policy retrieval abstains while no approved corpus is configured.
- FPT outage is visible and never triggers a hidden external fallback.
- The Vietnamese interface is case-centered and does not use chat as case truth.
- The generic single-agent baseline cannot write authoritative case state.
- No screen, API, report, test, or document claims that the system approves/rejects credit or is production-ready.
