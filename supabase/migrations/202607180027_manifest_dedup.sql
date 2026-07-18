-- Idempotency key for public.agent_context_manifests (master design section
-- 10.2, P0 #12 completion).
--
-- The table shipped (202607180010) with only a non-unique (case_id,
-- case_version, created_at) lookup index.  A specialist processor persists its
-- context manifest BEFORE the inference call, and a redelivery that crashed
-- after persisting the manifest but before the INFERENCE checkpoint re-runs the
-- pre-inference stage and re-persists.  Because the manifest content is
-- deterministic for a bound (case, version, task) -- the same goal contract,
-- versions, authorization snapshot and refs -- ``compute_context_hash`` is
-- stable across that redelivery, so an ``on conflict`` on (task_id,
-- context_hash) resolves the retry to the existing row instead of a second
-- write.
--
-- KEY DECISION: a PARTIAL unique index ``where task_id is not null``.  Every
-- specialist/agent manifest is task-bound, so this dedupes exactly the rows a
-- re-delivered task may legitimately reproduce.  An unbound orchestration/
-- planning manifest (task_id null) is deliberately excluded: Postgres already
-- treats null-task rows as distinct under a plain unique index, and the partial
-- predicate makes that intent explicit rather than incidental.  The
-- (task_id, context_hash) pair -- not (case, version, task) -- is the key
-- because the Independent Risk Review legitimately writes TWO manifests per
-- task (a blind Pass A and a checker Pass B) whose content hashes differ; a
-- (case, version, task) key would reject the second.

-- Defensively remove any pre-existing duplicates (keep the lowest id per
-- group).  The table is append-only via a trigger that rejects every DELETE, so
-- the trigger is disabled for the surgical cleanup and restored immediately.
alter table public.agent_context_manifests
  disable trigger agent_context_manifests_are_append_only;

delete from public.agent_context_manifests as duplicate
using public.agent_context_manifests as keeper
where duplicate.task_id is not null
  and duplicate.task_id = keeper.task_id
  and duplicate.context_hash = keeper.context_hash
  and duplicate.id > keeper.id;

alter table public.agent_context_manifests
  enable trigger agent_context_manifests_are_append_only;

create unique index agent_context_manifests_task_hash_key
  on public.agent_context_manifests (task_id, context_hash)
  where task_id is not null;
