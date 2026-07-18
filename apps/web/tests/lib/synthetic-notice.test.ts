// The canonical synthetic-data notice is a safety contract (AGENTS.md).
// shared/synthetic-notice.json is the single source of truth; the frontend
// constants must match it exactly (master design P0 #10).
import { describe, expect, it } from "vitest";

import sharedNotice from "../../../../shared/synthetic-notice.json";
import {
  SYNTHETIC_DATA_NOTICE,
  SYNTHETIC_DATA_NOTICE_VI,
} from "../../components/shell/synthetic-data-notice";

describe("canonical synthetic-data notice", () => {
  it("matches the shared English source of truth", () => {
    expect(SYNTHETIC_DATA_NOTICE).toBe(sharedNotice.en);
  });

  it("matches the shared Vietnamese source of truth", () => {
    expect(SYNTHETIC_DATA_NOTICE_VI).toBe(sharedNotice.vi);
  });
});
