import React from "react";

import { RepaymentWorkspace } from "../../../../components/repayments/repayment-workspace";

interface RepaymentPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function RepaymentPage({ params }: RepaymentPageProps) {
  const { caseId } = await params;
  return <RepaymentWorkspace caseId={caseId} />;
}
