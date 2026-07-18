import React from "react";

import { GapWorkspace } from "../../../../components/gaps/gap-list";

interface GapsPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function GapsPage({ params }: GapsPageProps) {
  const { caseId } = await params;
  return <GapWorkspace caseId={caseId} />;
}
