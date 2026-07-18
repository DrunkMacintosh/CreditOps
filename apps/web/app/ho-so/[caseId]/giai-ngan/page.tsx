import React from "react";

import { DisbursementWorkspace } from "../../../../components/disbursements/disbursement-workspace";

interface DisbursementPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function DisbursementPage({ params }: DisbursementPageProps) {
  const { caseId } = await params;
  return <DisbursementWorkspace caseId={caseId} />;
}
