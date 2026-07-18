-- Stage 2 (master design section 5 stage 2, section 13): the versioned
-- FinancingRequest gains its structured intake fields and the
-- HG_FINANCING_NEED_CONFIRMED human gate joins the closed gate registry.
--
-- PROPOSED / ASSUMPTION: 'HG_FINANCING_NEED_CONFIRMED' is a SYNTHETIC gate name.
-- It has NO official SHB role mapping, approval delegation, or control-code and
-- is presented only as a demonstration application control, exactly like the
-- existing G1..G4 synthetic gates (202607180001_orchestration_graph_gates.sql).
-- Additive only: the new gate is a superset of the prior CHECK set, so every
-- existing human_gates row remains valid.

-- 1. Extend the human_gates gate-type registry.  The prior CHECK was declared
--    inline on the column, so Postgres named it public.human_gates_gate_type_check.
--    Dropping and re-adding keeps the additive, one-superset-of-the-other
--    semantics: no existing gate type is removed.
alter table public.human_gates
  drop constraint human_gates_gate_type_check;

alter table public.human_gates
  add constraint human_gates_gate_type_check check (
    gate_type in (
      'G1_INTAKE_COMPLETE',
      'G2_GAP_REQUEST_APPROVAL',
      'G3_RISK_DISPOSITION',
      'G4_OPS_AUTHORIZATION',
      'HG_FINANCING_NEED_CONFIRMED'
    )
  );

comment on constraint human_gates_gate_type_check on public.human_gates is
  'PROPOSED synthetic gate registry (no official SHB mapping). '
  'HG_FINANCING_NEED_CONFIRMED is the stage-2 financing-need confirmation gate; '
  'it is human-satisfied only and gates the (later, PROPOSED) intake-completion '
  'coupling, never an existing task-graph node.';

-- 2. Stage-2 structured financing-request fields.  financing_requests is ALREADY
--    versioned (request_version, unique (case_id, request_version)), append-only
--    (reject_append_only_mutation on update/delete) and RLS-scoped to the active
--    case assignment (202607170008_financing_requests.sql); only the structured
--    columns are new here.  Every field is NULLABLE: a NULL is the durable
--    UNKNOWN / NOT_PROVIDED marker.  The spec forbids model-invented values, so a
--    field the customer did not supply MUST stay NULL rather than be defaulted.
--    (requested_amount and purpose_vi already exist as the required amount and
--    purpose; they are intentionally left unchanged.)
alter table public.financing_requests
  add column currency text
    check (currency is null or length(btrim(currency)) between 1 and 8),
  add column product_vi text
    check (product_vi is null or length(btrim(product_vi)) > 0),
  add column term_months integer
    check (term_months is null or term_months > 0),
  add column expected_use_date date,
  add column repayment_source_vi text
    check (repayment_source_vi is null or length(btrim(repayment_source_vi)) > 0),
  add column repayment_plan_vi text
    check (repayment_plan_vi is null or length(btrim(repayment_plan_vi)) > 0),
  add column proposed_security_vi text
    check (proposed_security_vi is null or length(btrim(proposed_security_vi)) > 0),
  add column customer_own_funds numeric(30, 0)
    check (customer_own_funds is null or customer_own_funds >= 0),
  add column connected_trade_products_vi text
    check (
      connected_trade_products_vi is null
      or length(btrim(connected_trade_products_vi)) > 0
    ),
  add column working_capital_cycle_vi text
    check (
      working_capital_cycle_vi is null
      or length(btrim(working_capital_cycle_vi)) > 0
    ),
  add column key_suppliers_customers_vi text
    check (
      key_suppliers_customers_vi is null
      or length(btrim(key_suppliers_customers_vi)) > 0
    ),
  add column proposed_cash_flow_controls_vi text
    check (
      proposed_cash_flow_controls_vi is null
      or length(btrim(proposed_cash_flow_controls_vi)) > 0
    );

comment on column public.financing_requests.currency is
  'PROPOSED stage-2 field. NULL = UNKNOWN / NOT_PROVIDED (never a model-invented value).';
comment on column public.financing_requests.customer_own_funds is
  'Customer own funds (vốn tự có), exact whole-currency decimal. NULL = NOT_PROVIDED.';
