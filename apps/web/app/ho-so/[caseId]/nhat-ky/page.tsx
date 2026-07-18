import React from "react";

import { AuditWorkspace } from "../../../../components/audit/audit-timeline";

interface AuditPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function AuditPage({ params }: AuditPageProps) {
  const { caseId } = await params;
  return <AuditWorkspace caseId={caseId} />;
}
