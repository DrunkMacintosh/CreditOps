-- Agent-governance schema foundation (master design sections 10.1, 10.2, 13).
-- Two append-only tables underpin every bounded model call:
--
--   * public.goal_contracts          -- the immutable, versioned GoalContract:
--     the objective, allowed/prohibited actions, success conditions, required
--     evidence, output schema, optional required human gate and budget that
--     bound one agent execution.  An agent can never widen its own goal, and
--     history is never rewritten.
--   * public.agent_context_manifests -- the persisted, hashable snapshot of
--     exactly what one model call was authorized to see, carrying its stable
--     context hash for version-keyed cache invalidation and audit replay.
--
-- Both are service-role only: the workforce never reads governance internals,
-- so there is deliberately no authenticated RLS policy (mirrors
-- public.outbox_events).  The universal-prohibition superset rule and the
-- context-hash derivation live in the domain layer
-- (services/api/src/creditops/domain/goal_contracts.py), the semantic
-- authority; this migration is only the durable schema foundation.  All data
-- is synthetic.

create table public.goal_contracts (
  id uuid primary key default gen_random_uuid(),
  -- A stable semantic key (e.g. 'underwriting-assessment') plus a
  -- monotonically-versioned revision.
  contract_key text not null check (length(btrim(contract_key)) > 0),
  version integer not null check (version > 0),
  objective_vi text not null check (length(btrim(objective_vi)) > 0),
  allowed_actions jsonb not null default '[]'::jsonb
    check (jsonb_typeof(allowed_actions) = 'array'),
  -- Every contract must name what it may NOT do: a non-empty prohibition set.
  -- The domain model additionally enforces that it is a superset of the
  -- universal human-only bans (master design section 3.2).
  prohibited_actions jsonb not null
    check (
      jsonb_typeof(prohibited_actions) = 'array'
      and jsonb_array_length(prohibited_actions) > 0
    ),
  success_conditions_vi jsonb not null default '[]'::jsonb
    check (jsonb_typeof(success_conditions_vi) = 'array'),
  required_evidence_kinds jsonb not null default '[]'::jsonb
    check (jsonb_typeof(required_evidence_kinds) = 'array'),
  output_schema_ref text not null check (length(btrim(output_schema_ref)) > 0),
  output_schema_version text not null check (length(btrim(output_schema_version)) > 0),
  required_human_gate text
    check (required_human_gate is null or length(btrim(required_human_gate)) > 0),
  max_input_tokens integer not null check (max_input_tokens > 0),
  max_output_tokens integer not null check (max_output_tokens > 0),
  max_tool_calls integer not null check (max_tool_calls > 0),
  created_at timestamptz not null default clock_timestamp(),
  -- One row per (contract_key, version); revisions never overwrite.
  constraint goal_contracts_key_version_key unique (contract_key, version)
);

create index goal_contracts_key_idx
  on public.goal_contracts (contract_key, version);

create trigger goal_contracts_are_append_only
before update or delete on public.goal_contracts
for each row execute function public.reject_append_only_mutation();

alter table public.goal_contracts enable row level security;
alter table public.goal_contracts force row level security;

revoke all on public.goal_contracts from public, anon, authenticated;
grant all on public.goal_contracts to service_role;


create table public.agent_context_manifests (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references public.credit_cases(id) on delete restrict,
  case_version integer not null check (case_version > 0),
  -- Optional task binding: an orchestration/planning manifest can exist before
  -- any specialist task.  The composite FK is MATCH SIMPLE, so a null task_id
  -- skips the check, while a present task_id must match the exact task row
  -- (and therefore its case + case version), exactly like the specialist
  -- output tables.
  task_id uuid,
  -- The exact goal contract + version this call was bound to.  Recorded as an
  -- audit snapshot (no hard FK to goal_contracts), consistent with the other
  -- opaque refs the manifest carries.
  goal_contract_id uuid not null,
  goal_contract_version integer not null check (goal_contract_version > 0),
  agent_role text not null check (length(btrim(agent_role)) > 0),
  profile_version text not null check (length(btrim(profile_version)) > 0),
  prompt_version text not null check (length(btrim(prompt_version)) > 0),
  schema_version text not null check (length(btrim(schema_version)) > 0),
  model_version text
    check (model_version is null or length(btrim(model_version)) between 1 and 200),
  -- The deterministic content hash (domain: compute_context_hash); never null.
  context_hash text not null check (length(btrim(context_hash)) > 0),
  -- The full ordered manifest snapshot: refs only, never inline document text.
  manifest jsonb not null check (jsonb_typeof(manifest) = 'object'),
  created_at timestamptz not null default clock_timestamp(),
  constraint agent_context_manifests_task_case_fk
    foreign key (task_id, case_id, case_version)
    references public.processing_tasks(id, case_id, case_version)
    on delete restrict
);

create index agent_context_manifests_case_idx
  on public.agent_context_manifests (case_id, case_version, created_at);

create trigger agent_context_manifests_are_append_only
before update or delete on public.agent_context_manifests
for each row execute function public.reject_append_only_mutation();

alter table public.agent_context_manifests enable row level security;
alter table public.agent_context_manifests force row level security;

revoke all on public.agent_context_manifests from public, anon, authenticated;
grant all on public.agent_context_manifests to service_role;
