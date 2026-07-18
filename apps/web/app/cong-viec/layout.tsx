import React, { type ReactNode } from "react";

import { AppShell } from "../../components/shell/app-shell";

export default function WorkQueueLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
