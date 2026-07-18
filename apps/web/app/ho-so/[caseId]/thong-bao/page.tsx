import React from "react";

import { NotificationWorkspace } from "../../../../components/notifications/notification-workspace";

interface NotificationPageProps {
  params: Promise<{ caseId: string }>;
}

export default async function NotificationPage({ params }: NotificationPageProps) {
  const { caseId } = await params;
  return <NotificationWorkspace caseId={caseId} />;
}
