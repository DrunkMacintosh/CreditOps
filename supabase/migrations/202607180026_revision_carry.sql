-- Revision carry for the MAKER_MUST_REVISE loop (master design section 9).
--
-- A case-version bump (application/use_cases/request_maker_revision.py ->
-- infrastructure/postgres/orchestration.py bump_case_version) re-issues the
-- intake handoff at the NEW version so G1_INTAKE_COMPLETE stays satisfied and
-- the maker/legal analysis reruns while the intake evidence carries forward
-- unchanged.  The re-issue is an INSERT..SELECT that PRESERVES the original
-- DOCUMENT_INGESTION source_task_id (the evidence did not change, so its
-- provenance must not be fabricated).
--
-- On a live Postgres that INSERT hit the failed-closed boundary documented in
-- bump_case_version: ``handoffs_task_case_fk`` bound (source_task_id, case_id,
-- case_version) to public.processing_tasks, so a clone at the NEW version found
-- no matching task and was rejected.
--
-- Cloning the intake evidence chain forward instead (so a v2 ingestion task and
-- its document version exist) is IMPOSSIBLE, not merely out of scope:
--   * a DOCUMENT_INGESTION task is document-scoped (processing_tasks_document_scope
--     forces document_version_id NOT NULL) to a public.document_versions row;
--   * document_versions identity is unique on (document_id, version) AND on
--     (storage_bucket, storage_object_key) and is trigger-immutable, so no honest
--     new row can be minted at a new case_version (the document version number
--     and its physical storage object do not change in a revision);
--   * the whole evidence graph is deliberately version-fenced -- see
--     supabase/tests/version_integrity_test.sql, which asserts that binding a page
--     region / processing task / checkpoint / retrieval passage to a document
--     version from another case_version raises 23503; and confirmed_facts are
--     derived, not inserted, by derive_and_protect_confirmed_fact(), which forces
--     case_version := candidate.case_version.
--
-- A handoff, however, is a WORKFLOW provenance pointer, not evidence.  Re-issuing
-- it at the new version pointing at the ORIGINAL intake ingestion task is the
-- honest record that intake did not change.  So version-decouple exactly that
-- pointer while keeping it case-fenced: a handoff still may not anchor on a task
-- from another case, but a re-issued handoff at a new case_version may anchor on
-- the ingestion task recorded at the version where intake completed.
--
-- This migration touches ONLY the handoff source pointer.  It does not relax any
-- evidence FK or the confirmed-fact derivation, so the version-fencing invariants
-- proven by version_integrity_test.sql and confirmed_facts_test.sql are preserved.

-- 1. Back the version-decoupled FK with a case-scoped unique on the task
--    identity.  ``id`` is already the primary key, so (id, case_id) is trivially
--    unique; the explicit constraint just lets a foreign key reference exactly
--    those two columns.
alter table public.processing_tasks
  add constraint processing_tasks_id_case_key unique (id, case_id);

-- 2. Relax the handoff source FK from (source_task_id, case_id, case_version) to
--    (source_task_id, case_id).  Every existing row satisfied the stricter form,
--    so it satisfies this one; NOT VALID + VALIDATE follows the repository's
--    migration convention (see 202607180001) and keeps the rewrite cheap.
alter table public.handoffs
  drop constraint handoffs_task_case_fk;
alter table public.handoffs
  add constraint handoffs_task_case_fk
  foreign key (source_task_id, case_id)
  references public.processing_tasks (id, case_id)
  on delete restrict
  not valid;
alter table public.handoffs
  validate constraint handoffs_task_case_fk;
