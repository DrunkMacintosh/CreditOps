import React from "react";

import { SettlementWorkspace } from "../../../../components/settlement/settlement-workspace";

interface SettlementPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function SettlementPage({ params }: SettlementPageProps) {
  const { caseId } = await params;
  return <SettlementWorkspace caseId={caseId} />;
}
