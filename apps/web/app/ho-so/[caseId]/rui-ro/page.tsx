import React from "react";

import { RiskReviewDesk } from "../../../../components/risk/risk-review-desk";

interface RiskReviewPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function RiskReviewPage({ params }: RiskReviewPageProps) {
  const { caseId } = await params;
  return <RiskReviewDesk caseId={caseId} />;
}
