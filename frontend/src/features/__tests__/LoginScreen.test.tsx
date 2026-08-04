import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import { LoginScreen } from "../LoginScreen";

describe("LoginScreen", () => {
  it("renders the public product page by default", () => {
    render(
      <MemoryRouter>
        <LoginScreen onLogin={() => undefined} />
      </MemoryRouter>
    );
    expect(
      screen.getByText("Take a strategy from idea to evidence before any capital is at risk.")
    ).toBeInTheDocument();
  });

  it("renders the credential card with a heading and labelled fields", () => {
    render(
      <MemoryRouter>
        <LoginScreen onLogin={() => undefined} />
      </MemoryRouter>
    );
    expect(screen.getByRole("heading", { name: "Sign in to your workspace" })).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toBeInTheDocument();
  });

  it("states the simulation boundary on the public page", () => {
    render(
      <MemoryRouter>
        <LoginScreen onLogin={() => undefined} />
      </MemoryRouter>
    );
    expect(
      screen.getByText(/No broker is connected, no real-money orders are placed/i)
    ).toBeInTheDocument();
  });
});
