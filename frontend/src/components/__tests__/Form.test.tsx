import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Checkbox, Select, TextField } from "../Form";

describe("TextField", () => {
  it("renders with label and input", () => {
    render(<TextField id="test-input" label="Name" value="" onChange={() => undefined} />);
    expect(screen.getByLabelText("Name")).toBeInTheDocument();
  });

  it("calls onChange when typed into", async () => {
    const onChange = vi.fn();
    render(<TextField id="test-input" label="Name" value="" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Name"), "a");
    expect(onChange).toHaveBeenCalledWith("a");
  });

  it("displays error message", () => {
    render(<TextField id="test-input" label="Name" value="" onChange={() => undefined} error="Required" />);
    expect(screen.getByText("Required")).toBeInTheDocument();
  });

  it("displays hint when no error", () => {
    render(<TextField id="test-input" label="Name" value="" onChange={() => undefined} hint="Enter your name" />);
    expect(screen.getByText("Enter your name")).toBeInTheDocument();
  });
});

describe("Select", () => {
  it("renders with label and options", () => {
    render(
      <Select id="test-select" label="Method" value="a" onChange={() => undefined}>
        <option value="a">Option A</option>
        <option value="b">Option B</option>
      </Select>
    );
    expect(screen.getByLabelText("Method")).toBeInTheDocument();
    expect(screen.getByText("Option A")).toBeInTheDocument();
  });
});

describe("Checkbox", () => {
  it("renders with label", () => {
    render(<Checkbox id="test-check" label="Enable" checked={false} onChange={() => undefined} />);
    expect(screen.getByLabelText("Enable")).toBeInTheDocument();
  });

  it("calls onChange with checked state", async () => {
    const onChange = vi.fn();
    render(<Checkbox id="test-check" label="Enable" checked={false} onChange={onChange} />);
    await userEvent.click(screen.getByLabelText("Enable"));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});
