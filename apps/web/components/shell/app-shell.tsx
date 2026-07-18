import Link from "next/link";
import React, { type ReactNode } from "react";

import { MobileNav } from "./mobile-nav";
import { SyntheticDataNotice } from "./synthetic-data-notice";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#noi-dung-chinh">
        Chuyển đến nội dung chính
      </a>
      <header className="app-header">
        <Link aria-label="CreditOps EvidenceGraph, Danh sách hồ sơ" className="brand" href="/ho-so">
          <span aria-hidden="true" className="brand-mark">CE</span>
          <span>
            <strong>CreditOps</strong>
            <small>EvidenceGraph</small>
          </span>
        </Link>
        <nav aria-label="Điều hướng chính" className="primary-nav">
          <Link href="/cong-viec">Hàng việc của tôi</Link>
          <Link href="/ho-so">Hồ sơ</Link>
        </nav>
        <MobileNav>
          <Link href="/cong-viec">Hàng việc của tôi</Link>
          <Link href="/ho-so">Hồ sơ</Link>
        </MobileNav>
      </header>
      <div className="authority-boundary" role="note">
        Hệ thống hỗ trợ chuẩn bị và rà soát bằng chứng; không phê duyệt hoặc từ chối cấp tín dụng. Con người có thẩm quyền quyết định.
      </div>
      <main className="app-main" id="noi-dung-chinh" tabIndex={-1}>
        {children}
      </main>
      <footer className="app-footer">
        <SyntheticDataNotice />
      </footer>
    </div>
  );
}
