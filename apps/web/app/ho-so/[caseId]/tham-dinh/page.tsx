import React from "react";

import { UnderwritingWorksheet } from "../../../../components/underwriting/underwriting-worksheet";

interface UnderwritingPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function UnderwritingPage({ params }: UnderwritingPageProps) {
  const { caseId } = await params;
  return <UnderwritingWorksheet caseId={caseId} />;
}
