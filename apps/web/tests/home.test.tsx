import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import React from "react";

import Home from "../app/page";

it("shows the Vietnamese intake boundary and synthetic notice", () => {
  render(<Home />);
  expect(
    screen.getByRole("heading", { name: "Tiếp nhận hồ sơ tín dụng" }),
  ).toBeVisible();
  expect(
    screen.getByText(/All customer data, policies, documents/),
  ).toBeVisible();
});
