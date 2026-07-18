import React from "react";

import { HandoffWorkspace } from "../../../../components/handoff/handoff-summary";

interface HandoffPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function HandoffPage({ params }: HandoffPageProps) {
  const { caseId } = await params;
  return <HandoffWorkspace caseId={caseId} />;
}
