-- Stage 8 (master design section 5 giai đoạn 8 "Đàm phán và ký kết hồ sơ tín
-- dụng"): deterministic contract packages, versioned redlines and MOCK signature
-- evidence, plus the three stage-8 human gates.
--
-- PROPOSED / ASSUMPTION: 'HG_CONTRACT_PACKAGE_APPROVED',
-- 'HG_SIGNATURE_AUTHORITY_CONFIRMED' and 'HG_CONTRACTS_SIGNED' are SYNTHETIC gate
-- names.  They carry NO official SHB role mapping, signing-authority matrix, or
-- control-code and are presented only as demonstration application controls,
-- exactly like the existing G1..G4 and the other HG_ gates.  Additive only: the
-- CHECK re-declared below is a strict SUPERSET of every prior gate registry
-- (202607180012 / ...16 / ...17 / ...18 / ...19), so every existing human_gates
-- row stays valid -- including 'HG_CREDIT_NOTIFICATION_APPROVED' (added in ...17),
-- 'HG_DISBURSEMENT_CONDITIONS_CONFIRMED' (added in ...18) and
-- 'HG_SECURITY_PERFECTION_CONFIRMED' (added in ...19), all of which this union
-- carries so nothing that ran before this migration is dropped.
--
-- Everything a package renders is DETERMINISTIC: the content is built by the
-- domain template renderer from the credit decision's ApprovedTermSnapshot (the
-- model never invents a clause).  Redlines are append-only versioned rows, never
-- edits; a package whose term-snapshot hash diverges from the current decision
-- snapshot is fenced in state 'MATERIAL_CHANGE_DETECTED', which BLOCKS all three
-- gates (the case must return to stage 6 for a new decision -- a deferred loop,
-- recorded here as the blocking state only).  Real e-sign / contract execution
-- is OUT OF SCOPE: signing records are MOCK signature evidence only.
--
-- All customer data, policies, documents, and banking-system responses in this
-- project are synthetic and created solely for demonstration.

-- Extend the human_gates gate-type registry.  Union of every prior registry
-- (heals the ...18/...19 divergence where ...19 dropped ...18's disbursement
-- gate) plus the three new stage-8 gates.  Drop/re-add keeps the additive,
-- one-superset-of-the-other semantics: no existing gate type is removed.
alter table public.human_gates
  drop constraint human_gates_gate_type_check;

alter table public.human_gates
  add constraint human_gates_gate_type_check check (
    gate_type in (
      'G1_INTAKE_COMPLETE',
      'G2_GAP_REQUEST_APPROVAL',
      'G3_RISK_DISPOSITION',
      'G4_OPS_AUTHORIZATION',
      'HG_FINANCING_NEED_CONFIRMED',
      'HG_UNDERWRITING_ASSESSMENT_REVIEWED',
      'HG_LEGAL_ASSESSMENT_REVIEWED',
      'HG_MAKER_SUBMISSION_CONFIRMED',
      'HG_CREDIT_NOTIFICATION_APPROVED',
      'HG_DISBURSEMENT_CONDITIONS_CONFIRMED',
      'HG_SECURITY_PERFECTION_CONFIRMED',
      'HG_CONTRACT_PACKAGE_APPROVED',
      'HG_SIGNATURE_AUTHORITY_CONFIRMED',
      'HG_CONTRACTS_SIGNED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). Union superset of '
  'all prior registries plus the three stage-8 gates: '
  'HG_CONTRACT_PACKAGE_APPROVED (ops checker approves the rendered package), '
  'HG_SIGNATURE_AUTHORITY_CONFIRMED (signing authority confirmed) and '
  'HG_CONTRACTS_SIGNED (MOCK signing recorded). All human-satisfied only and NOT '
  'required_gate on any task-graph node -- coupling orchestration readiness to '
  'them is a deferred decision.';

-- One append-only, deterministically rendered contract-package VERSION.  A new
-- package_version row is written per change; rows are never edited.  ``state``
-- names the lifecycle of THAT version (there is no 'SIGNED' state: "signed" is
-- the presence of a contract_signature_evidence row on a READY_FOR_SIGNATURE
-- package).  ``term_snapshot_hash`` binds the version to the exact approved-term
-- snapshot it was rendered from (the material-change detector's input);
-- ``content_hash`` is the sha256 of ``content_vi`` (computed in the domain).
create table public.contract_packages (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  decision_id uuid not null
    references public.human_credit_decisions(id) on delete restrict,
  term_snapshot_hash char(64) not null
    check (term_snapshot_hash ~ '^[0-9a-f]{64}$'),
  content_vi text not null check (length(btrim(content_vi)) > 0),
  content_hash char(64) not null check (content_hash ~ '^[0-9a-f]{64}$'),
  package_version integer not null check (package_version >= 1),
  state text not null check (
    state in (
      'DRAFT', 'REDLINED', 'MATERIAL_CHANGE_DETECTED', 'READY_FOR_SIGNATURE'
    )
  ),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  -- A new package_version per change; a duplicate version for the same
  -- (case, version) is rejected.
  constraint contract_packages_case_version_key
    unique (case_id, case_version, package_version),
  -- Referenced by the child tables' composite FKs so a redline / evidence row
  -- can never drift onto a different case version than its package.
  constraint contract_packages_id_case_version_key
    unique (id, case_id, case_version)
);

create index contract_packages_case_idx
  on public.contract_packages (case_id, case_version, package_version desc);

create trigger contract_packages_are_append_only
before update or delete on public.contract_packages
for each row execute function public.reject_append_only_mutation();

alter table public.contract_packages enable row level security;
alter table public.contract_packages force row level security;

create policy contract_packages_select_assigned
on public.contract_packages
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = contract_packages.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.contract_packages from public, anon, authenticated;
grant select on public.contract_packages to authenticated;
grant all on public.contract_packages to service_role;

-- One append-only versioned redline against a package.  A redline is NEVER an
-- edit: it records the human's change note plus the replacement content, and its
-- write also appends a new REDLINED package version (same transaction, adapter).
-- ``redline_version`` is unique per package.
create table public.contract_redlines (
  id uuid primary key default gen_random_uuid(),
  package_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  redline_version integer not null check (redline_version >= 1),
  change_note_vi text not null check (length(btrim(change_note_vi)) > 0),
  changed_content_vi text not null check (length(btrim(changed_content_vi)) > 0),
  changed_content_hash char(64) not null
    check (changed_content_hash ~ '^[0-9a-f]{64}$'),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint contract_redlines_package_fk
    foreign key (package_id, case_id, case_version)
    references public.contract_packages(id, case_id, case_version)
    on delete restrict,
  -- At most one redline row per (package, redline_version).
  constraint contract_redlines_package_version_key
    unique (package_id, redline_version)
);

create index contract_redlines_package_idx
  on public.contract_redlines (package_id, redline_version);

create trigger contract_redlines_are_append_only
before update or delete on public.contract_redlines
for each row execute function public.reject_append_only_mutation();

alter table public.contract_redlines enable row level security;
alter table public.contract_redlines force row level security;

create policy contract_redlines_select_assigned
on public.contract_redlines
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = contract_redlines.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.contract_redlines from public, anon, authenticated;
grant select on public.contract_redlines to authenticated;
grant all on public.contract_redlines to service_role;

-- One append-only MOCK signing record, 1:1 with a signable package version.
-- OUT OF SCOPE: real e-sign / execution is never performed -- ``kind`` is the
-- closed {MOCK_SIGNATURE} set and ``signer_names`` is a non-empty JSON array.
create table public.contract_signature_evidence (
  id uuid primary key default gen_random_uuid(),
  package_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  kind text not null check (kind in ('MOCK_SIGNATURE')),
  signer_names jsonb not null check (
    jsonb_typeof(signer_names) = 'array' and jsonb_array_length(signer_names) >= 1
  ),
  evidence_note_vi text,
  recorded_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint contract_signature_evidence_package_fk
    foreign key (package_id, case_id, case_version)
    references public.contract_packages(id, case_id, case_version)
    on delete restrict,
  -- 1:1 with the package version it signs.
  constraint contract_signature_evidence_package_key unique (package_id)
);

create index contract_signature_evidence_case_idx
  on public.contract_signature_evidence (case_id, case_version, created_at desc);

create trigger contract_signature_evidence_are_append_only
before update or delete on public.contract_signature_evidence
for each row execute function public.reject_append_only_mutation();

alter table public.contract_signature_evidence enable row level security;
alter table public.contract_signature_evidence force row level security;

create policy contract_signature_evidence_select_assigned
on public.contract_signature_evidence
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = contract_signature_evidence.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.contract_signature_evidence from public, anon, authenticated;
grant select on public.contract_signature_evidence to authenticated;
grant all on public.contract_signature_evidence to service_role;
