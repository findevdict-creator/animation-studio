import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { badCells, FitReport } from "../components/FitReport";
import { OutfitSpecForm } from "../components/OutfitSpecForm";

afterEach(cleanup);

const REPORT = {
  ok: false, failed: ["no_clipping:lower_shorts"], pose_suite: ["rest", "sit", "crouch"],
  wearer: { kind: "character", name: "Ali", fit_profile: "child_average" },
  checks: [
    { name: "no_clipping:lower_shorts", ok: false, poses: ["crouch"] },
    { name: "deformation:lower_shorts", ok: true, poses: [] },
    { name: "body_mask_modifier", ok: true },
  ],
  poses: { lower_shorts: {
    rest: { tested: 80, clipped: 0, clip_depth_m: 0, stretch_lo: 0.9, stretch_hi: 1.1 },
    sit: { tested: 80, clipped: 0, clip_depth_m: 0, stretch_lo: 0.5, stretch_hi: 1.7 },
    crouch: { tested: 80, clipped: 4, clip_depth_m: 0.0045, stretch_lo: 0.4, stretch_hi: 1.9 },
  } },
};

describe("FitReport", () => {
  it("marks the poses each failed garment check names", () => {
    expect([...badCells(REPORT)]).toEqual(["lower_shorts/crouch"]);
  });

  it("shows the wearer, the verdict and a garment by pose grid", () => {
    render(<FitReport report={REPORT} />);
    expect(screen.getByText(/Fit check on Ali: failed \(2\/3 checks\)/)).toBeTruthy();
    const cells = screen.getAllByRole("cell");
    expect(cells.map((c) => c.className)).toEqual(["ok", "ok", "bad"]);
    expect(cells[2].textContent).toContain("4 clip");
    expect(cells[2].getAttribute("title")).toContain("depth 4.5 mm");
  });

  it("renders nothing without a report", () => {
    const { container } = render(<FitReport report={null} />);
    expect(container.innerHTML).toBe("");
  });
});

const GARMENTS = [
  { id: "GARM-TSHIRT", version: "0.1", key: "tshirt", slot: "upper", label: "T-shirt", binding: "skinned",
    params: { ease: { min: 0, max: 1, default: 0.2, description: "looseness" } }, fabrics: ["cotton", "jersey"],
    default_fabric: "jersey", default_color: "white", trim: true, covers: ["torso"], hides: [] },
  { id: "GARM-SHORTS", version: "0.1", key: "shorts", slot: "lower", label: "Shorts", binding: "skinned", params: {},
    fabrics: ["cotton"], default_fabric: "cotton", default_color: "navy", trim: false, covers: ["pelvis"], hides: [] },
  { id: "GARM-TROUSERS", version: "0.1", key: "trousers", slot: "lower", label: "Trousers", binding: "skinned", params: {},
    fabrics: ["cotton"], default_fabric: "cotton", default_color: "navy", trim: false, covers: ["pelvis"], hides: [] },
];
const CLOTHING = { style_profile: "family_cartoon@1.0", colors: { white: "#ffffff", royal_blue: "#2a4fd6" }, fabrics: {},
  clearance_m: [0.004, 0.022], min_clearance_m: 0.002, mask_margin_m: 0.025, fit: {}, poses: [],
  fit_profiles: ["child_average", "adult_average"] };

function withData(ui: React.ReactNode) {
  const qc = new QueryClient();
  qc.setQueryData(["garments"], GARMENTS);
  qc.setQueryData(["clothing", "family_cartoon@1.0"], CLOTHING);
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>;
}

describe("OutfitSpecForm", () => {
  const spec = { fit_profiles: ["child_average"], pieces: [{ slot: "upper", garment: { id: "GARM-TSHIRT", version: "0.1" } }] };

  it("offers one garment per slot from the catalog and swaps a slot's garment", () => {
    const onChange = vi.fn();
    render(withData(<OutfitSpecForm spec={spec} onChange={onChange} />));
    fireEvent.change(screen.getByLabelText("lower garment"), { target: { value: "GARM-TROUSERS" } });
    expect(onChange.mock.calls[0][0].pieces).toEqual([
      { slot: "upper", garment: { id: "GARM-TSHIRT", version: "0.1" } },
      { slot: "lower", garment: { id: "GARM-TROUSERS", version: "0.1" } },
    ]);
    fireEvent.change(screen.getByLabelText("upper garment"), { target: { value: "" } });
    expect(onChange.mock.calls[1][0].pieces).toEqual([]);
  });

  it("edits parameters within their range and the trim colour only where the garment has one", () => {
    const onChange = vi.fn();
    render(withData(<OutfitSpecForm spec={spec} onChange={onChange} />));
    const ease = screen.getByRole("slider") as HTMLInputElement;
    expect([ease.min, ease.max, ease.value]).toEqual(["0", "1", "0.2"]);
    fireEvent.change(ease, { target: { value: "0.4" } });
    expect(onChange.mock.calls[0][0].pieces[0].params).toEqual({ ease: 0.4 });
    expect(screen.getByText("Trim colour")).toBeTruthy();
  });
});
