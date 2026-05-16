import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Notice, NoticeBanner } from "../Notice";

describe("Notice", () => {
  it("renders children text", () => {
    render(<Notice tone="info">Something happened</Notice>);
    expect(screen.getByText("Something happened")).toBeInTheDocument();
  });

  it("uses the correct CSS class for error tone", () => {
    render(<Notice tone="error">Error</Notice>);
    expect(screen.getByText("Error").parentElement).toHaveClass("inline-error");
  });

  it("uses the correct CSS class for success tone", () => {
    render(<Notice tone="success">Success</Notice>);
    expect(screen.getByText("Success").parentElement).toHaveClass("inline-success");
  });

  it("shows dismiss button and calls onDismiss", async () => {
    const onDismiss = vi.fn();
    render(<Notice tone="info" onDismiss={onDismiss}>Dismiss me</Notice>);
    const dismissBtn = screen.getByLabelText("Dismiss notice");
    expect(dismissBtn).toBeInTheDocument();
    await userEvent.click(dismissBtn);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});

describe("NoticeBanner", () => {
  it("renders title and children", () => {
    render(<NoticeBanner tone="info" title="Notice title">Notice body</NoticeBanner>);
    expect(screen.getByText("Notice title")).toBeInTheDocument();
    expect(screen.getByText("Notice body")).toBeInTheDocument();
  });
});
