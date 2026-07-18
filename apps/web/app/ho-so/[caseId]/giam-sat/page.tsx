import React from "react";

import { MonitoringWorkspace } from "../../../../components/monitoring/monitoring-workspace";

interface MonitoringPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function MonitoringPage({ params }: MonitoringPageProps) {
  const { caseId } = await params;
  return <MonitoringWorkspace caseId={caseId} />;
}
