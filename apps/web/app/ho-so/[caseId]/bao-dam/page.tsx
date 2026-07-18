import React from "react";

import { SecurityWorkspace } from "../../../../components/security/security-workspace";

interface SecurityPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function SecurityPage({ params }: SecurityPageProps) {
  const { caseId } = await params;
  return <SecurityWorkspace caseId={caseId} />;
}
