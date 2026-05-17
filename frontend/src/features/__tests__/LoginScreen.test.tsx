import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { LoginScreen } from "../LoginScreen";

describe("LoginScreen", () => {
  it("renders the main landing page by default", () => {
    render(<LoginScreen onLogin={() => undefined} />);
    expect(screen.getByText("Research, validate, and paper trade strategies from one premium workspace.")).toBeInTheDocument();
  });

  it("renders login card with heading", () => {
    render(<LoginScreen onLogin={() => undefined} />);
    expect(screen.getByText("Enter your workspace")).toBeInTheDocument();
  });
});
