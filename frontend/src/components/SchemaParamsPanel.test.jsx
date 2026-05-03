import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  ChipGroup,
  Toggle,
  NumberPresets,
  FieldRenderer,
  SchemaParamsPanel,
} from "./SchemaParamsPanel.jsx";

// ---------- ChipGroup ----------------------------------------------------

describe("ChipGroup", () => {

  it("renders label, hint, and all options", () => {
    render(
      <ChipGroup
        label="Quality"
        hint="render fidelity"
        options={["low", "high"]}
        value="low"
        onChange={() => {}}
      />
    );
    expect(screen.getByText("Quality")).toBeInTheDocument();
    expect(screen.getByText("render fidelity")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "low" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "high" })).toBeInTheDocument();
  });

  it("greys disallowed options but still renders them", () => {
    render(
      <ChipGroup
        label="Size"
        options={["a", "b"]}
        value="a"
        onChange={() => {}}
        isOptionAllowed={(opt) => opt === "a"}
      />
    );
    const a = screen.getByRole("button", { name: "a" });
    const b = screen.getByRole("button", { name: "b" });
    expect(a).toBeEnabled();
    expect(b).toBeDisabled();
    expect(b).toHaveAttribute("aria-disabled", "true");
  });

  it("does not call onChange when disabled chip clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ChipGroup
        label="Size"
        options={["a", "b"]}
        value="a"
        onChange={onChange}
        isOptionAllowed={(opt) => opt === "a"}
      />
    );
    await user.click(screen.getByRole("button", { name: "b" }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it("calls onChange with the option value when an enabled chip clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ChipGroup
        label="Size"
        options={["a", "b"]}
        value="a"
        onChange={onChange}
        isOptionAllowed={() => true}
      />
    );
    await user.click(screen.getByRole("button", { name: "b" }));
    expect(onChange).toHaveBeenCalledWith("b");
  });

  it("renders disabled-reason hint when fieldDisabled", () => {
    render(
      <ChipGroup
        label="Size"
        options={["a"]}
        value={null}
        onChange={() => {}}
        fieldDisabled
        disabledReason="No tier supports this."
      />
    );
    expect(screen.getByText("No tier supports this.")).toBeInTheDocument();
  });

  it("forwards data-test-* attributes for Playwright targeting", () => {
    const { container } = render(
      <ChipGroup
        label="Size"
        options={["a"]}
        value={null}
        onChange={() => {}}
        dataTestField="size"
        dataTestGroup="primary"
      />
    );
    const root = container.querySelector('[data-test-field="size"]');
    expect(root).toBeInTheDocument();
    expect(root).toHaveAttribute("data-test-group", "primary");
    expect(root).toHaveAttribute("data-test-disabled", "false");
  });
});

// ---------- Toggle -------------------------------------------------------

describe("Toggle", () => {

  it("renders label and hint", () => {
    render(
      <Toggle
        label="Image search"
        hint="ground on web images"
        value={false}
        onChange={() => {}}
      />
    );
    expect(screen.getByText("Image search")).toBeInTheDocument();
    expect(screen.getByText("ground on web images")).toBeInTheDocument();
  });

  it("checkbox reflects value prop", () => {
    const { rerender } = render(
      <Toggle label="x" value={false} onChange={() => {}} />
    );
    expect(screen.getByRole("checkbox")).not.toBeChecked();
    rerender(<Toggle label="x" value={true} onChange={() => {}} />);
    expect(screen.getByRole("checkbox")).toBeChecked();
  });

  it("disabled checkbox cannot be toggled", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <Toggle
        label="x"
        value={false}
        onChange={onChange}
        fieldDisabled
        disabledReason="Provider has not enabled this feature."
      />
    );
    await user.click(screen.getByRole("checkbox"));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText("Provider has not enabled this feature."))
      .toBeInTheDocument();
  });
});

// ---------- NumberPresets -----------------------------------------------

describe("NumberPresets", () => {

  it("renders label, ticker, and presets", () => {
    const { container } = render(
      <NumberPresets
        label="Output count"
        hint="per generation"
        presets={[1, 2, 4, 8]}
        max={10}
        value={4}
        onChange={() => {}}
      />
    );
    expect(screen.getByText("Output count")).toBeInTheDocument();
    expect(screen.getByText("per generation")).toBeInTheDocument();
    // ticker shows the current value
    expect(container.querySelector("[data-test-output-count]"))
      .toHaveTextContent("4");
    // four preset buttons
    for (const n of [1, 2, 4, 8]) {
      expect(screen.getByRole("button", { name: String(n) })).toBeInTheDocument();
    }
  });

  it("disables presets above max", () => {
    render(
      <NumberPresets
        label="Output count"
        presets={[1, 2, 4, 8]}
        max={4}
        value={1}
        onChange={() => {}}
      />
    );
    expect(screen.getByRole("button", { name: "1" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "4" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "8" })).toBeDisabled();
  });

  it("clicking a preset fires onChange with the number", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <NumberPresets
        label="Output count"
        presets={[1, 2, 4, 8]}
        max={10}
        value={1}
        onChange={onChange}
      />
    );
    await user.click(screen.getByRole("button", { name: "4" }));
    expect(onChange).toHaveBeenCalledWith(4);
  });
});

// ---------- FieldRenderer ------------------------------------------------

describe("FieldRenderer", () => {

  const mkPlan = (over = {}) => ({
    field: {
      k: "size",
      control: "chip-row",
      label: "Size",
      options: ["a", "b"],
      group: "primary",
      order: 10,
      ...over,
    },
    allowedOptions: new Set(["a", "b"]),
    fieldDisabled: false,
    disabledReason: null,
  });

  it("routes chip-row → ChipGroup", () => {
    render(
      <FieldRenderer
        plan={mkPlan()}
        value="a"
        onChange={() => {}}
        capabilities={{ size: ["a", "b"] }}
      />
    );
    expect(screen.getByText("Size")).toBeInTheDocument();
  });

  it("routes number → NumberPresets and uses cap as max", () => {
    const plan = {
      field: {
        k: "n_max",
        value_key: "n",
        control: "number",
        label: "Output count",
        group: "primary",
        order: 10,
        min: 1,
        max: 10,
        presets: [1, 2, 4, 8],
      },
      allowedOptions: null,
      fieldDisabled: false,
      disabledReason: null,
    };
    render(
      <FieldRenderer
        plan={plan}
        value={2}
        onChange={() => {}}
        capabilities={{ n_max: 4 }}
      />
    );
    // 8 disabled because cap is 4
    expect(screen.getByRole("button", { name: "8" })).toBeDisabled();
  });

  it("routes toggle → Toggle", () => {
    const plan = {
      field: {
        k: "image_search",
        control: "toggle",
        label: "Image search",
        group: "advanced",
        order: 30,
      },
      allowedOptions: null,
      fieldDisabled: false,
      disabledReason: null,
    };
    render(
      <FieldRenderer
        plan={plan}
        value={false}
        onChange={() => {}}
        capabilities={{ image_search: true }}
      />
    );
    expect(screen.getByRole("checkbox")).not.toBeChecked();
  });

  it("returns null for unknown control", () => {
    const plan = {
      field: { k: "x", control: "mystery", label: "X" },
      allowedOptions: null,
      fieldDisabled: false,
      disabledReason: null,
    };
    const { container } = render(
      <FieldRenderer plan={plan} value={null} onChange={() => {}} capabilities={{}} />
    );
    expect(container.firstChild).toBeNull();
  });
});

// ---------- SchemaParamsPanel -------------------------------------------

describe("SchemaParamsPanel", () => {

  const fullPlan = {
    primary: [
      {
        field: {
          k: "n_max", value_key: "n", control: "number", label: "Output count",
          group: "primary", order: 10, min: 1, max: 10, presets: [1, 2, 4, 8],
        },
        allowedOptions: null, fieldDisabled: false, disabledReason: null,
      },
      {
        field: {
          k: "size", control: "chip-row", label: "Size",
          options: ["a", "b"], group: "primary", order: 20,
        },
        allowedOptions: new Set(["a", "b"]),
        fieldDisabled: false, disabledReason: null,
      },
    ],
    advanced: [
      {
        field: {
          k: "image_search", control: "toggle", label: "Image search",
          group: "advanced", order: 10,
        },
        allowedOptions: null, fieldDisabled: false, disabledReason: null,
      },
    ],
  };

  it("renders fallback message for an empty plan", () => {
    render(
      <SchemaParamsPanel
        plan={{ primary: [], advanced: [] }}
        params={{}}
        setParam={() => {}}
        capabilities={{}}
      />
    );
    expect(
      screen.getByText("This model has no configurable parameters.")
    ).toBeInTheDocument();
  });

  it("renders all primary fields and the Advanced summary", () => {
    render(
      <SchemaParamsPanel
        plan={fullPlan}
        params={{ n: 1 }}
        setParam={() => {}}
        capabilities={{ n_max: 10, size: ["a"], image_search: true }}
      />
    );
    expect(screen.getByText("Output count")).toBeInTheDocument();
    expect(screen.getByText("Size")).toBeInTheDocument();
    expect(screen.getByText("◢ Advanced")).toBeInTheDocument();
  });

  it("does not render <details> when no advanced fields", () => {
    const planNoAdv = { primary: fullPlan.primary, advanced: [] };
    const { container } = render(
      <SchemaParamsPanel
        plan={planNoAdv}
        params={{}}
        setParam={() => {}}
        capabilities={{ n_max: 4, size: ["a"] }}
      />
    );
    expect(container.querySelector("details")).toBeNull();
  });

  it("setParam is called with the value_key, not the field's k", async () => {
    const user = userEvent.setup();
    const setParam = vi.fn();
    render(
      <SchemaParamsPanel
        plan={fullPlan}
        params={{ n: 1 }}
        setParam={setParam}
        capabilities={{ n_max: 10, size: ["a", "b"], image_search: true }}
      />
    );
    // click "4" preset under Output count — value_key=n → setParam("n", 4)
    await user.click(screen.getByRole("button", { name: "4" }));
    expect(setParam).toHaveBeenCalledWith("n", 4);
    expect(setParam).not.toHaveBeenCalledWith("n_max", expect.anything());
  });
});
