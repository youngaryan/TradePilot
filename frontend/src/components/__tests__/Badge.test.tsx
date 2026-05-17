import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge } from "../Badge";

describe("Badge", () => {
  it("renders with label", () => {
    render(<Badge label="test badge" />);
    expect(screen.getByText("test badge")).toBeInTheDocument();
  });

  it("applies neutral tone by default", () => {
    render(<Badge label="default" />);
    expect(screen.getByText("default")).toHaveClass("badge--neutral");
  });

  it("applies the given tone class", () => {
    render(<Badge label="good" tone="good" />);
    expect(screen.getByText("good")).toHaveClass("badge--good");
  });
});
