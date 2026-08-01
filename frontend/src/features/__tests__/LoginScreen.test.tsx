import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { LoginScreen } from "../LoginScreen";

describe("LoginScreen", () => {
  it("renders the main landing page by default", () => {
    render(
      <MemoryRouter>
        <LoginScreen onLogin={() => undefined} />
      </MemoryRouter>
    );
    expect(screen.getByText("Research, validate, and paper trade strategies from one premium workspace.")).toBeInTheDocument();
  });

  it("renders login card with heading", () => {
    render(
      <MemoryRouter>
        <LoginScreen onLogin={() => undefined} />
      </MemoryRouter>
    );
    expect(screen.getByText("Enter your workspace")).toBeInTheDocument();
  });
});
