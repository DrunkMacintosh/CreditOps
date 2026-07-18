import React from "react";

import { ContractWorkspace } from "../../../../components/contracts/contract-workspace";

interface ContractPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function ContractPage({ params }: ContractPageProps) {
  const { caseId } = await params;
  return <ContractWorkspace caseId={caseId} />;
}
