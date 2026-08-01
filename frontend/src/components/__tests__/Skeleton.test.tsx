import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Skeleton, SkeletonCard } from "../Skeleton";

describe("Skeleton", () => {
  it("renders a line variant by default", () => {
    const { getByTestId } = render(<Skeleton />);
    expect(getByTestId("skeleton")).toBeInTheDocument();
  });

  it("renders circle variant", () => {
    const { getByTestId } = render(<Skeleton variant="circle" />);
    expect(getByTestId("skeleton")).toBeInTheDocument();
  });

  it("renders card variant", () => {
    const { getByTestId } = render(<Skeleton variant="card" />);
    expect(getByTestId("skeleton-card")).toBeInTheDocument();
  });

  it("applies custom width and height", () => {
    const { getByTestId } = render(<Skeleton width={200} height={40} />);
    const el = getByTestId("skeleton");
    expect(el).toHaveStyle("width: 200px");
    expect(el).toHaveStyle("height: 40px");
  });

  it("is aria-hidden", () => {
    const { getByTestId } = render(<Skeleton />);
    expect(getByTestId("skeleton")).toHaveAttribute("aria-hidden", "true");
  });
});

describe("SkeletonCard", () => {
  it("renders the specified number of lines plus a title line", () => {
    const { getAllByTestId } = render(<SkeletonCard lines={3} />);
    expect(getAllByTestId("skeleton-line").length).toBe(4);
  });
});
