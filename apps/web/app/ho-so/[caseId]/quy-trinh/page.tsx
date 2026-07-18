import React from "react";

import { OrchestrationConsole } from "../../../../components/orchestration/orchestration-console";

interface OrchestrationPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function OrchestrationPage({ params }: OrchestrationPageProps) {
  const { caseId } = await params;
  return <OrchestrationConsole caseId={caseId} />;
}
