import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Skeleton, SkeletonCard } from "../Skeleton";

describe("Skeleton", () => {
  it("renders a line variant by default", () => {
    const { container } = render(<Skeleton />);
    expect(container.querySelector(".skeleton--line")).toBeInTheDocument();
  });

  it("renders circle variant", () => {
    const { container } = render(<Skeleton variant="circle" />);
    expect(container.querySelector(".skeleton--circle")).toBeInTheDocument();
  });

  it("renders card variant", () => {
    const { container } = render(<Skeleton variant="card" />);
    expect(container.querySelector(".skeleton--card")).toBeInTheDocument();
  });

  it("applies custom width and height", () => {
    const { container } = render(<Skeleton width={200} height={40} />);
    const el = container.querySelector(".skeleton--line");
    expect(el).toHaveStyle("width: 200px");
    expect(el).toHaveStyle("height: 40px");
  });

  it("is aria-hidden", () => {
    const { container } = render(<Skeleton />);
    expect(container.querySelector(".skeleton--line")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("SkeletonCard", () => {
  it("renders the specified number of lines plus a title line", () => {
    const { container } = render(<SkeletonCard lines={3} />);
    expect(container.querySelectorAll(".skeleton--line").length).toBe(4);
  });
});
