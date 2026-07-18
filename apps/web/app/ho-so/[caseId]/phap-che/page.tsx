import React from "react";

import { LegalAssessmentScreen } from "../../../../components/legal/legal-assessment";

interface LegalPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function LegalPage({ params }: LegalPageProps) {
  const { caseId } = await params;
  return <LegalAssessmentScreen caseId={caseId} />;
}
