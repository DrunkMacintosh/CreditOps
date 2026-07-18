-- Stage 12 (master design section 5 giai đoạn 12 "Quản lý khoản vay và giám sát
-- sau cấp tín dụng"): the deterministic post-credit monitoring store --
-- longitudinal observations, generated obligations, covenant tests, and the
-- early-warning alerts raised by deterministic rules (never by a model).
--
--   * public.monitoring_obligations -- append-only, deterministically generated
--     reporting/monitoring obligations (a frequency + a due date + requirement).
--   * public.monitoring_observations -- the append-only LONGITUDINAL memory.  It
--     separates three timestamps: effective_at (when the observed fact holds in
--     the world) and observed_at (when a human/source observed it) are CALLER
--     data; recorded_at is the trusted DATABASE clock (default clock_timestamp()).
--     History is never overwritten.
--   * public.covenants -- append-only covenants, each carrying its OWN declared
--     threshold (operator + value + version).  A restated threshold is a NEW row
--     with a higher threshold_version, never an edit.  The threshold is versioned
--     synthetic data a human supplies; nothing is hard-coded.
--   * public.covenant_tests -- append-only test rows whose pass/fail is EXACTLY
--     the declared comparison of supplied numeric inputs against the covenant
--     threshold (exact numeric cross-multiplication -- comparison_lhs vs
--     comparison_rhs -- echoed on the row).
--   * public.early_warning_alerts -- alert candidates raised by DETERMINISTIC
--     rules only (COVENANT_BREACH, OVERDUE_OBLIGATION), with a guarded human
--     disposition lifecycle; and public.alert_dispositions -- the append-only,
--     mandatory-rationale disposition trail.
--
-- TEMPORAL INVARIANT: only effective_at <= observed_at is enforced (a
-- deterministic fact about caller data).  observed_at <= recorded_at is
-- deliberately NOT a CHECK: it compares an UNTRUSTED client clock to the DB
-- clock, so it cannot be enforced deterministically -- recorded_at is instead the
-- authoritative persistence time regardless of any client-supplied timestamp.
--
-- DETERMINISTIC ALERT LIFECYCLE: an early_warning_alerts row's status may be
-- UPDATED only via an allowed edge; a BEFORE UPDATE trigger encodes the SAME map
-- as services/api/src/creditops/domain/monitoring.py::ALLOWED_ALERT_TRANSITIONS
-- and rejects (a) any UPDATE that changes a column other than status (42501) and
-- (b) any status pair not in the map (23514).  Deletes are forbidden (42501).
-- Every disposition is a HUMAN act carrying a mandatory rationale
-- (alert_dispositions.rationale_vi NOT NULL).  This is the human control of stage
-- 12; there is NO new gate.
--
-- NO DEBT CLASSIFICATION: this migration deliberately adds NO column, enum, or
-- value that classifies a debt (no nhóm nợ / NPL / provision bucket).  The spec
-- forbids it -- formal classification is OUT OF SCOPE and only early-warning
-- SIGNALS are surfaced for a human.
--
-- PROPOSED / SYNTHETIC: the frequency set, the operator set, the alert taxonomy
-- and its lifecycle, and every threshold value are a prototype configuration with
-- NO official SHB monitoring-policy mapping, to be reconfigured when an official
-- source exists.  This migration adds NO human_gates gate type.
--
-- All customer data, covenants, and thresholds in this project are synthetic and
-- created solely for demonstration.

-- 1. Monitoring obligations (append-only, deterministically generated).
create table public.monitoring_obligations (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  sequence integer not null check (sequence > 0),
  -- CLOSED PROPOSED synthetic frequency set (design giai đoạn 12).
  frequency text not null check (frequency in ('MONTHLY', 'QUARTERLY')),
  due_date date not null,
  requirement_text_vi text not null check (length(btrim(requirement_text_vi)) > 0),
  created_at timestamptz not null default clock_timestamp(),
  -- Referenced by the observations + alerts composite FKs so a child can never
  -- drift onto a different case version than its obligation.
  constraint monitoring_obligations_id_case_version_key
    unique (id, case_id, case_version)
);

create index monitoring_obligations_case_idx
  on public.monitoring_obligations (case_id, case_version, sequence);

create trigger monitoring_obligations_are_append_only
before update or delete on public.monitoring_obligations
for each row execute function public.reject_append_only_mutation();

alter table public.monitoring_obligations enable row level security;
alter table public.monitoring_obligations force row level security;

create policy monitoring_obligations_select_assigned
on public.monitoring_obligations
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = monitoring_obligations.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.monitoring_obligations from public, anon, authenticated;
grant select on public.monitoring_obligations to authenticated;
grant all on public.monitoring_obligations to service_role;

-- 2. Monitoring observations (append-only longitudinal memory, separated time).
create table public.monitoring_observations (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  -- Optional link to the obligation this observation reports against; the
  -- composite FK keeps it on the same case version as its obligation.
  obligation_id uuid,
  observation_type_vi text not null check (length(btrim(observation_type_vi)) > 0),
  body_vi text not null check (length(btrim(body_vi)) > 0),
  -- CALLER timestamps: when the fact holds (effective_at) and when it was
  -- observed (observed_at).
  effective_at timestamptz not null,
  observed_at timestamptz not null,
  -- DATABASE clock: the trusted persistence time.
  recorded_at timestamptz not null default clock_timestamp(),
  evidence_refs jsonb not null default '[]'::jsonb
    check (jsonb_typeof(evidence_refs) = 'array'),
  recorded_by uuid not null,
  recorded_by_role text not null check (length(btrim(recorded_by_role)) > 0),
  -- The ONLY deterministic temporal invariant (see the header): effective_at must
  -- not be after observed_at.  observed_at <= recorded_at is intentionally NOT a
  -- CHECK because it compares an untrusted client clock to the DB clock.
  constraint monitoring_observations_effective_before_observed
    check (effective_at <= observed_at),
  constraint monitoring_observations_obligation_fk
    foreign key (obligation_id, case_id, case_version)
    references public.monitoring_obligations(id, case_id, case_version)
    on delete restrict,
  constraint monitoring_observations_id_case_version_key
    unique (id, case_id, case_version)
);

create index monitoring_observations_case_idx
  on public.monitoring_observations (case_id, case_version, recorded_at);

create index monitoring_observations_obligation_idx
  on public.monitoring_observations (obligation_id)
  where obligation_id is not null;

create trigger monitoring_observations_are_append_only
before update or delete on public.monitoring_observations
for each row execute function public.reject_append_only_mutation();

alter table public.monitoring_observations enable row level security;
alter table public.monitoring_observations force row level security;

create policy monitoring_observations_select_assigned
on public.monitoring_observations
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = monitoring_observations.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.monitoring_observations from public, anon, authenticated;
grant select on public.monitoring_observations to authenticated;
grant all on public.monitoring_observations to service_role;

-- 3. Covenants (append-only; each row carries its OWN versioned threshold).
create table public.covenants (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  name_vi text not null check (length(btrim(name_vi)) > 0),
  metric_key text not null check (length(btrim(metric_key)) > 0),
  -- CLOSED PROPOSED synthetic operator set: a covenant passes when
  -- ratio OP threshold_value holds.
  operator text not null check (operator in ('GTE', 'GT', 'LTE', 'LT', 'EQ')),
  -- The declared threshold value (exact numeric) and its version.  Versioned,
  -- human-supplied synthetic data -- never hard-coded in the engine.
  threshold_value numeric not null,
  threshold_version integer not null check (threshold_version >= 1),
  created_by uuid not null,
  created_at timestamptz not null default clock_timestamp(),
  constraint covenants_id_case_version_key unique (id, case_id, case_version)
);

create index covenants_case_idx
  on public.covenants (case_id, case_version, created_at);

create trigger covenants_are_append_only
before update or delete on public.covenants
for each row execute function public.reject_append_only_mutation();

alter table public.covenants enable row level security;
alter table public.covenants force row level security;

create policy covenants_select_assigned
on public.covenants
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = covenants.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.covenants from public, anon, authenticated;
grant select on public.covenants to authenticated;
grant all on public.covenants to service_role;

-- 4. Covenant tests (append-only; the deterministic pass/fail with echoed math).
create table public.covenant_tests (
  id uuid primary key default gen_random_uuid(),
  covenant_id uuid not null,
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  metric_key text not null check (length(btrim(metric_key)) > 0),
  operator text not null check (operator in ('GTE', 'GT', 'LTE', 'LT', 'EQ')),
  -- Supplied numeric inputs; denominator must be strictly positive so the
  -- exact cross-multiplied comparison is equivalent to ratio OP threshold.
  numerator numeric not null,
  denominator numeric not null check (denominator > 0),
  threshold_value numeric not null,
  threshold_version integer not null check (threshold_version >= 1),
  -- The exact terms actually compared: comparison_lhs = numerator,
  -- comparison_rhs = threshold_value * denominator.
  comparison_lhs numeric not null,
  comparison_rhs numeric not null,
  passed boolean not null,
  tested_by uuid not null,
  tested_by_role text not null check (length(btrim(tested_by_role)) > 0),
  recorded_at timestamptz not null default clock_timestamp(),
  constraint covenant_tests_covenant_fk
    foreign key (covenant_id, case_id, case_version)
    references public.covenants(id, case_id, case_version)
    on delete restrict,
  constraint covenant_tests_id_case_version_key unique (id, case_id, case_version)
);

create index covenant_tests_case_idx
  on public.covenant_tests (case_id, case_version, recorded_at);

create index covenant_tests_covenant_idx
  on public.covenant_tests (covenant_id, recorded_at);

create trigger covenant_tests_are_append_only
before update or delete on public.covenant_tests
for each row execute function public.reject_append_only_mutation();

alter table public.covenant_tests enable row level security;
alter table public.covenant_tests force row level security;

create policy covenant_tests_select_assigned
on public.covenant_tests
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = covenant_tests.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.covenant_tests from public, anon, authenticated;
grant select on public.covenant_tests to authenticated;
grant all on public.covenant_tests to service_role;

-- 5. Early-warning alerts (raised by deterministic rules; guarded lifecycle).
create table public.early_warning_alerts (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  -- CLOSED PROPOSED synthetic rule set: the two DETERMINISTIC rules that may
  -- raise an alert.  A model never writes this table.
  rule text not null check (rule in ('COVENANT_BREACH', 'OVERDUE_OBLIGATION')),
  -- CLOSED PROPOSED synthetic lifecycle: OPEN is the only status a rule creates.
  status text not null default 'OPEN' check (
    status in ('OPEN', 'ACKNOWLEDGED', 'ESCALATED', 'DISMISSED_BY_HUMAN')
  ),
  detail_vi text not null check (length(btrim(detail_vi)) > 0),
  source_covenant_test_id uuid,
  source_obligation_id uuid,
  source_observation_id uuid,
  created_at timestamptz not null default clock_timestamp(),
  -- The source shape must match the rule: a covenant breach binds exactly the
  -- failed test; an overdue obligation binds exactly the obligation + the late
  -- observation.  No cross-shaped or source-less alert can exist.
  constraint early_warning_alerts_source_shape check (
    (rule = 'COVENANT_BREACH'
       and source_covenant_test_id is not null
       and source_obligation_id is null
       and source_observation_id is null)
    or (rule = 'OVERDUE_OBLIGATION'
       and source_covenant_test_id is null
       and source_obligation_id is not null
       and source_observation_id is not null)
  ),
  constraint early_warning_alerts_covenant_test_fk
    foreign key (source_covenant_test_id, case_id, case_version)
    references public.covenant_tests(id, case_id, case_version)
    on delete restrict,
  constraint early_warning_alerts_obligation_fk
    foreign key (source_obligation_id, case_id, case_version)
    references public.monitoring_obligations(id, case_id, case_version)
    on delete restrict,
  constraint early_warning_alerts_observation_fk
    foreign key (source_observation_id, case_id, case_version)
    references public.monitoring_observations(id, case_id, case_version)
    on delete restrict
);

create index early_warning_alerts_case_idx
  on public.early_warning_alerts (case_id, case_version, created_at);

-- Dedup: a covenant test raises at most one COVENANT_BREACH alert, and an
-- obligation carries at most one OVERDUE_OBLIGATION alert regardless of how many
-- late observations arrive.  The application inserts with ON CONFLICT DO NOTHING
-- against these partial unique indexes (defence in depth for concurrency).
create unique index early_warning_alerts_covenant_test_uidx
  on public.early_warning_alerts (source_covenant_test_id)
  where rule = 'COVENANT_BREACH';

create unique index early_warning_alerts_obligation_uidx
  on public.early_warning_alerts (source_obligation_id)
  where rule = 'OVERDUE_OBLIGATION';

-- DETERMINISTIC lifecycle enforcement.  On UPDATE: only status may change (else
-- 42501); the status pair must be an allowed edge in the same map as
-- domain/monitoring.py::ALLOWED_ALERT_TRANSITIONS (else 23514).  On DELETE:
-- forbidden (42501).  There is NO implicit edge and no self-transition.
create or replace function public.enforce_early_warning_alert_transition()
returns trigger
language plpgsql
security invoker
set search_path = pg_catalog
as $$
declare
  allowed text[];
begin
  if tg_op = 'DELETE' then
    raise exception using
      errcode = '42501',
      message = 'early warning alerts cannot be deleted';
  end if;

  -- Only status may ever change; identity/source/detail are frozen.
  if row(
    new.id, new.case_id, new.case_version, new.rule, new.detail_vi,
    new.source_covenant_test_id, new.source_obligation_id,
    new.source_observation_id, new.created_at
  ) is distinct from row(
    old.id, old.case_id, old.case_version, old.rule, old.detail_vi,
    old.source_covenant_test_id, old.source_obligation_id,
    old.source_observation_id, old.created_at
  ) then
    raise exception using
      errcode = '42501',
      message = 'only the status of an early warning alert may change';
  end if;

  allowed := case old.status
    when 'OPEN' then array['ACKNOWLEDGED', 'ESCALATED', 'DISMISSED_BY_HUMAN']
    when 'ACKNOWLEDGED' then array['ESCALATED', 'DISMISSED_BY_HUMAN']
    when 'ESCALATED' then array['DISMISSED_BY_HUMAN']
    when 'DISMISSED_BY_HUMAN' then array[]::text[]
    else array[]::text[]
  end;

  if not (new.status = any(allowed)) then
    raise exception using
      errcode = '23514',
      message = format(
        'forbidden early warning alert transition %s -> %s',
        old.status, new.status
      );
  end if;

  return new;
end;
$$;

revoke all on function public.enforce_early_warning_alert_transition() from public;

create trigger early_warning_alerts_enforce_transition
before update or delete on public.early_warning_alerts
for each row execute function public.enforce_early_warning_alert_transition();

alter table public.early_warning_alerts enable row level security;
alter table public.early_warning_alerts force row level security;

create policy early_warning_alerts_select_assigned
on public.early_warning_alerts
for select to authenticated using (
  exists (
    select 1 from public.case_assignments as assignment
    where assignment.case_id = early_warning_alerts.case_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.early_warning_alerts from public, anon, authenticated;
grant select on public.early_warning_alerts to authenticated;
grant all on public.early_warning_alerts to service_role;

-- 6. Alert dispositions (append-only; every disposition carries a rationale).
create table public.alert_dispositions (
  id uuid primary key default gen_random_uuid(),
  alert_id uuid not null
    references public.early_warning_alerts(id) on delete restrict,
  from_status text not null check (
    from_status in ('OPEN', 'ACKNOWLEDGED', 'ESCALATED', 'DISMISSED_BY_HUMAN')
  ),
  to_status text not null check (
    to_status in ('ACKNOWLEDGED', 'ESCALATED', 'DISMISSED_BY_HUMAN')
  ),
  -- Every alert disposition is a HUMAN act with a MANDATORY rationale (the human
  -- control of stage 12), enforced at the database.
  rationale_vi text not null check (length(btrim(rationale_vi)) > 0),
  actor_id uuid not null,
  actor_role text not null check (length(btrim(actor_role)) > 0),
  created_at timestamptz not null default clock_timestamp()
);

create index alert_dispositions_alert_idx
  on public.alert_dispositions (alert_id, created_at);

create trigger alert_dispositions_are_append_only
before update or delete on public.alert_dispositions
for each row execute function public.reject_append_only_mutation();

alter table public.alert_dispositions enable row level security;
alter table public.alert_dispositions force row level security;

-- RLS joins through the parent alert to the active case assignment (the child
-- carries no case_id of its own).
create policy alert_dispositions_select_assigned
on public.alert_dispositions
for select to authenticated using (
  exists (
    select 1
    from public.early_warning_alerts as alert
    join public.case_assignments as assignment
      on assignment.case_id = alert.case_id
    where alert.id = alert_dispositions.alert_id
      and assignment.officer_id = (select auth.uid())
      and assignment.revoked_at is null
  )
);

revoke all on public.alert_dispositions from public, anon, authenticated;
grant select on public.alert_dispositions to authenticated;
grant all on public.alert_dispositions to service_role;
