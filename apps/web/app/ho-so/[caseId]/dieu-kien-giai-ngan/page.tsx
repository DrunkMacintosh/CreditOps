import React from "react";

import { ConditionWorkspace } from "../../../../components/conditions/condition-workspace";

interface ConditionPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function ConditionPage({ params }: ConditionPageProps) {
  const { caseId } = await params;
  return <ConditionWorkspace caseId={caseId} />;
}
