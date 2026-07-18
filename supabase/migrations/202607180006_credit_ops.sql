-- PROPOSED package store for the Credit Operations Agent (the fifth and
-- last specialist role).  Packages are append-only assembled evidence +
-- draft memo output, never a decision: the payload schema has no
-- approve/reject/decision/disbursement/sign-off/execute/send field anywhere
-- (services/api/src/creditops/domain/credit_ops.py), and both new human
-- write surfaces (action authorization, document-request approval) are
-- FIRST-CLASS append-only tables so a human record can bind per-action or
-- per-request without ever editing or deleting the package row.  The agent
-- NEVER satisfies G2_GAP_REQUEST_APPROVAL or G4_OPS_AUTHORIZATION; only an
-- authorized human write (recorded here) can, and the gate derivation lives
-- in application code (application/orchestration/gates.py), never in this
-- migration.  All data is synthetic.
--
-- This migration grants NO write access -- and no additional read access --
-- on public.underwriting_assessments, public.legal_compliance_assessments,
-- or public.risk_review_assessments.  The agent's read-only access to those
-- tables is enforced by the application-layer port
-- (application/ports/credit_ops.py), not by new SQL here; this migration
-- only ever selects from them in application code through the existing
-- service_role grant those tables already carry.

create table public.credit_ops_packages (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  task_id uuid not null,
  execution_id uuid not null,
  agent_role text not null default 'CREDIT_OPERATIONS'
    check (agent_role = 'CREDIT_OPERATIONS'),
  prompt_version text not null check (length(btrim(prompt_version)) > 0),
  model_id text not null check (length(btrim(model_id)) > 0),
  endpoint_id text not null check (length(btrim(endpoint_id)) > 0),
  package jsonb not null check (jsonb_typeof(package) = 'object'),
  package_schema_version text not null default 'credit-ops-package-v1'
    check (length(btrim(package_schema_version)) > 0),
  evidence_view_built_at timestamptz not null,
  created_at timestamptz not null default clock_timestamp(),
  -- Composite case FK through the source task, exactly like every other
  -- specialist output table: the package binds to the exact task row (and
  -- therefore case + case version) that produced it.
  constraint credit_ops_packages_task_case_fk
    foreign key (task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict,
  -- One durable package per (case, version, task): duplicate delivery
  -- resolves to the existing row instead of a second package.
  constraint credit_ops_packages_task_key
    unique (case_id, case_version, task_id),
  constraint credit_ops_packages_time_order
    check (evidence_view_built_at <= created_at),
  -- Referenced by the authorization/approval tables' composite FKs below.
  constraint credit_ops_packages_id_case_version_key
    unique (id, case_id, case_version)
);

create index credit_ops_packages_case_idx
  on public.credit_ops_packages (case_id, case_version, created_at desc);

create trigger credit_ops_packages_are_append_only
before update or delete on public.credit_ops_packages
for each row execute function public.reject_append_only_mutation();

alter table public.credit_ops_packages enable row level security;
alter table public.credit_ops_packages force row level security;

create policy credit_ops_packages_select_assigned
on public.credit_ops_packages
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = credit_ops_packages.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.credit_ops_packages from public, anon, authenticated;

grant select on public.credit_ops_packages to authenticated;

grant all on public.credit_ops_packages to service_role;

-- Human authorization of one proposed action (identified only by the
-- ``action_id`` UUID embedded in the package's JSON ``proposed_actions``
-- array -- there is deliberately no first-class "proposed_actions" table;
-- the application layer validates ``action_id`` against the latest
-- package's JSON before inserting here, exactly as the risk-review
-- disposition API validates a challenge id against JSON).  Append-only,
-- actor id + role captured.  Authorization only RECORDS authority; it never
-- executes anything -- there is no executor code path anywhere in this
-- codebase, and this table has no status/outcome column to record one in.
create table public.ops_action_authorizations (
  id uuid primary key default gen_random_uuid(),
  package_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  action_id uuid not null,
  actor_id uuid not null,
  actor_role text not null check (length(btrim(actor_role)) > 0),
  rationale_vi text not null check (length(btrim(rationale_vi)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  constraint ops_action_authorizations_package_fk
    foreign key (package_id, case_id, case_version)
    references public.credit_ops_packages(id, case_id, case_version)
    on delete restrict,
  -- At most one authorization per (package, action): repeated authorize
  -- calls for the same action are idempotent no-ops at the application
  -- layer, never a second competing record.
  constraint ops_action_authorizations_package_action_key
    unique (package_id, action_id)
);

create index ops_action_authorizations_package_idx
  on public.ops_action_authorizations (package_id, created_at);

create trigger ops_action_authorizations_are_append_only
before update or delete on public.ops_action_authorizations
for each row execute function public.reject_append_only_mutation();

alter table public.ops_action_authorizations enable row level security;
alter table public.ops_action_authorizations force row level security;

create policy ops_action_authorizations_select_assigned
on public.ops_action_authorizations
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = ops_action_authorizations.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.ops_action_authorizations from public, anon, authenticated;

grant select on public.ops_action_authorizations to authenticated;

grant all on public.ops_action_authorizations to service_role;

-- Human approval of one drafted document request (identified only by the
-- ``request_id`` UUID embedded in the package's JSON ``document_requests``
-- array; same no-first-class-table rationale as above).  Append-only.
-- Approving flips ONLY the derived, read-time ``approval_status`` view for
-- that request -- it never mutates the package row the request lives in,
-- and there is no send/dispatch column or mechanism anywhere in this
-- migration or the application code that reads it.
create table public.document_request_approvals (
  id uuid primary key default gen_random_uuid(),
  package_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  request_id uuid not null,
  actor_id uuid not null,
  actor_role text not null check (length(btrim(actor_role)) > 0),
  rationale_vi text not null check (length(btrim(rationale_vi)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  constraint document_request_approvals_package_fk
    foreign key (package_id, case_id, case_version)
    references public.credit_ops_packages(id, case_id, case_version)
    on delete restrict,
  constraint document_request_approvals_package_request_key
    unique (package_id, request_id)
);

create index document_request_approvals_package_idx
  on public.document_request_approvals (package_id, created_at);

create trigger document_request_approvals_are_append_only
before update or delete on public.document_request_approvals
for each row execute function public.reject_append_only_mutation();

alter table public.document_request_approvals enable row level security;
alter table public.document_request_approvals force row level security;

create policy document_request_approvals_select_assigned
on public.document_request_approvals
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = document_request_approvals.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.document_request_approvals from public, anon, authenticated;

grant select on public.document_request_approvals to authenticated;

grant all on public.document_request_approvals to service_role;

-- Additive handoff-state extension: the operations->human-decision package.
-- All existing states remain valid; nothing existing is rewritten.
-- G2_GAP_REQUEST_APPROVAL and G4_OPS_AUTHORIZATION are never satisfied by
-- this handoff or by any agent write -- only a human authorization/approval
-- (above) can move them, via the deterministic derivations in
-- application/orchestration/gates.py.
alter table public.handoffs
  drop constraint handoffs_state_check;
alter table public.handoffs
  add constraint handoffs_state_check
  check (
    state in (
      'READY_FOR_SPECIALIST_REVIEW', 'READY_FOR_RISK_REVIEW', 'READY_FOR_OPERATIONS',
      'READY_FOR_HUMAN_DECISION'
    )
  );
