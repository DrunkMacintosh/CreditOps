import React, { type ReactNode } from "react";

// Zero-JS disclosure nav for narrow viewports. details/summary keeps the
// header a Server Component; CSS shows this only below 561px and shows the
// inline nav only above, so exactly one nav is in the accessibility tree.
export function MobileNav({ children }: { children: ReactNode }) {
  return (
    <details className="mobile-nav">
      <summary aria-label="Menu điều hướng">
        <span aria-hidden="true" className="mobile-nav-icon" />
      </summary>
      <nav aria-label="Điều hướng chính" className="mobile-nav-panel">
        {children}
      </nav>
    </details>
  );
}
