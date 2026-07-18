import React from "react";

import { DocumentReviewLoader } from "../../../../../components/review/document-review";

interface DocumentReviewPageProps {
  params: Promise<{ caseId: string; documentId: string }>;
}

export default async function DocumentReviewPage({
  params,
}: DocumentReviewPageProps) {
  const { caseId, documentId } = await params;
  return <DocumentReviewLoader caseId={caseId} documentId={documentId} />;
}
