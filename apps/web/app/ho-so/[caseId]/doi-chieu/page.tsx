import React from "react";

import { EvidenceDashboard } from "../../../../components/evidence/fact-ledger";

interface EvidencePageProps {
  params: Promise<{ caseId: string }>;
}

export default async function EvidencePage({ params }: EvidencePageProps) {
  const { caseId } = await params;
  return <EvidenceDashboard caseId={caseId} />;
}
