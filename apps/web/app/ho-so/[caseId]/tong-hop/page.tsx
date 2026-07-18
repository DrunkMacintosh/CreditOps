import React from "react";

import { CreditOpsDesk } from "../../../../components/credit-ops/credit-ops-desk";

interface CreditOpsPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function CreditOpsPage({ params }: CreditOpsPageProps) {
  const { caseId } = await params;
  return <CreditOpsDesk caseId={caseId} />;
}
