-- Stage 4 & 5 (master design section 5 giai đoạn 4-5, section 6.1 rows 4-5):
-- the specialist human-review gates and the maker-submission gate join the
-- closed human_gates registry.
--
-- PROPOSED / ASSUMPTION: 'HG_UNDERWRITING_ASSESSMENT_REVIEWED',
-- 'HG_LEGAL_ASSESSMENT_REVIEWED' and 'HG_MAKER_SUBMISSION_CONFIRMED' are
-- SYNTHETIC gate names.  They carry NO official SHB role mapping, approval
-- delegation, or control-code and are presented only as demonstration
-- application controls, exactly like the existing G1..G4 and
-- HG_FINANCING_NEED_CONFIRMED synthetic gates
-- (202607180001_orchestration_graph_gates.sql, 202607180012_financing_gate.sql).
-- Additive only: the new registry is a strict superset of the prior CHECK set,
-- so every existing human_gates row remains valid.

-- Extend the human_gates gate-type registry.  The prior CHECK was last
-- re-declared in 202607180012_financing_gate.sql as the constraint
-- public.human_gates_gate_type_check; dropping and re-adding keeps the additive,
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
      'HG_MAKER_SUBMISSION_CONFIRMED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). '
  'HG_UNDERWRITING_ASSESSMENT_REVIEWED and HG_LEGAL_ASSESSMENT_REVIEWED are the '
  'stage-4 specialist-review gates (one per maker/reviewer assessment); '
  'HG_MAKER_SUBMISSION_CONFIRMED is the stage-5 maker submission gate. All three '
  'are human-satisfied only and, like the other HG_ gates, are NOT required_gate '
  'on any task-graph node -- coupling orchestration readiness to them is a '
  'deferred decision.';
