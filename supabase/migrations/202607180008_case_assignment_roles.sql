-- PROPOSED / synthetic multi-role assignment dimension for public.case_assignments.
--
-- This migration adds a `case_role` dimension so a single officer can hold several
-- distinct participant roles on one case (see design section 17.3). The role
-- vocabulary below is a CLOSED, SYNTHETIC set for prototype demonstrations; it has
-- NO official SHB role mapping. Official SHB role names, RACI, SoD and delegation of
-- authority remain an OPEN QUESTION (design section 24) and must fail closed until a
-- source is supplied. Assignment/delegation itself is an audited server command; this
-- schema only records the role dimension it writes.
--
-- Discipline: additive, append-only migration. It extends the existing flat
-- (case_id, officer_id) assignment table without rewriting prior migrations, and
-- assignment rows stay append-only-with-revoked_at exactly as before.

alter table public.case_assignments
  add column case_role text;

-- Closed synthetic role set. NULL passes this check during the pre-backfill window
-- below; the column is made NOT NULL only after existing rows are backfilled.
alter table public.case_assignments
  add constraint case_assignments_case_role_known
  check (
    case_role in (
      'INTAKE_OFFICER',
      'UNDERWRITER',
      'LEGAL_REVIEWER',
      'RISK_REVIEWER',
      'OPS_OFFICER',
      'OPS_CHECKER',
      'ACTION_AUTHORIZER',
      'MONITORING_OFFICER',
      'COLLECTIONS_OFFICER',
      'AUDITOR'
    )
  );

-- Backfill: the prototype only ever created INTAKE_OFFICER assignments.
update public.case_assignments
  set case_role = 'INTAKE_OFFICER'
  where case_role is null;

-- Forward inserts that predate an explicit assignment/delegation command default to
-- the single role the prototype uses; a real command must set the role explicitly.
alter table public.case_assignments
  alter column case_role set default 'INTAKE_OFFICER';

alter table public.case_assignments
  alter column case_role set not null;

-- Replace the flat one-officer-per-case uniqueness with per-role uniqueness so an
-- officer may hold several distinct roles while duplicate (case, officer, role) rows
-- stay rejected. The original constraint name comes from 202607170002.
--
-- fact_confirmations_assignment_fk (202607170004) depended on the flat unique
-- key and must go first.  It is a REDUNDANT, weaker layer: the
-- fact_confirmations_enforce_active_authority trigger already requires an
-- ACTIVE (non-revoked) assignment for the confirming officer — strictly
-- stronger than this FK, which never checked revoked_at.  With multi-role
-- assignments (case_id, officer_id) is no longer unique, so this FK form
-- cannot exist at all; the trigger remains the enforcement layer.
alter table public.fact_confirmations
  drop constraint fact_confirmations_assignment_fk;

alter table public.case_assignments
  drop constraint case_assignments_case_officer_key;

alter table public.case_assignments
  add constraint case_assignments_case_officer_role_key
  unique (case_id, officer_id, case_role);
