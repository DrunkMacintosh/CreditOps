-- Close the inbox-dedup gap on public.controlled_check_records.
--
-- Its four sibling reviewer/output stores (legal_compliance_assessments,
-- underwriting_assessments, risk_review_assessments, credit_ops_packages) each
-- carry a unique (case_id, case_version, task_id) key so a duplicate queue
-- delivery resolves to the existing row instead of a second write.  This table
-- shipped (202607180004) with only a non-unique lookup index, relying solely on
-- the application-level ``on conflict (id)`` guard keyed on the invocation id.
--
-- DEDUP KEY DECISION: (case_id, case_version, task_id, check_type), NOT the
-- siblings' (case_id, case_version, task_id).  One legal/compliance task
-- legitimately writes MULTIPLE controlled-check records -- exactly one per
-- check_type -- because application/legal/controlled_checks.run_controlled_checks
-- iterates the closed ControlledCheckType set {KYC, AML_WATCHLIST,
-- RELATED_PARTY} and persist_assessment (infrastructure/postgres/legal.py)
-- inserts one row per result under the same task_id.  A (case, version, task)
-- key would therefore reject the 2nd and 3rd legitimate checks of every task.
-- Adding check_type makes the key match the one row a re-delivered task may
-- legitimately produce per check type: it dedupes redelivery while permitting
-- the three distinct checks.  check_type is single-valued per task (the calls
-- map has one entry per type), so the four columns uniquely identify a row.

-- Defensively remove any pre-existing violations (keep the lowest id per
-- group).  The table is append-only via a trigger that rejects every DELETE, so
-- the trigger is disabled for the surgical cleanup and restored immediately.
alter table public.controlled_check_records
  disable trigger controlled_check_records_are_append_only;

delete from public.controlled_check_records as duplicate
using public.controlled_check_records as keeper
where duplicate.case_id = keeper.case_id
  and duplicate.case_version = keeper.case_version
  and duplicate.task_id = keeper.task_id
  and duplicate.check_type = keeper.check_type
  and duplicate.id > keeper.id;

alter table public.controlled_check_records
  enable trigger controlled_check_records_are_append_only;

alter table public.controlled_check_records
  add constraint controlled_check_records_task_key
  unique (case_id, case_version, task_id, check_type);
