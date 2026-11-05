import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PropDef } from "../api/client";
import { blankInstance } from "../pages/SceneEditorPage";
import { BLANK, cleanManifest } from "../pages/SceneEditorPage";
import { PropSpecForm, propSize } from "../components/PropSpecForm";
import { SceneSetEditor, startOf } from "../components/SceneSetEditor";
import { facing, Layout, SetPlan } from "../components/SetPlan";

afterEach(cleanup);

const TABLE: PropDef = {
  id: "PROPD-TABLE", version: "0.1", key: "table", label: "Table", category: "furniture", base_size: [1.2, 0.8, 0.72],
  params: { size: { min: 0, max: 1, default: 0.5, description: "" }, width: { min: 0, max: 1, default: 0.5, description: "" } },
  finishes: ["wood", "painted"], default_finish: "wood", default_color: "oak", accent: null, default_accent: null,
  solid: true, rests_on: "floor", surfaces: [{ name: "top", kind: "top", height_share: 1 }], setting: "any",
};

const LAYOUT: Layout = {
  kind: "room", size: [5, 4, 2.6], lighting: "indoor_day",
  solids: [["sofa_1", -0.3, 1.5, 1.8, 0.85, 0], ["table_1", -0.25, 0.56, 1.0, 0.6, 0]],
  tops: [["table_1", -0.25, 0.56, 1.0, 0.6, 0, 0.475]],
  seats: [["sofa_1", -0.3, 1.5, 1.8, 0.85, 0, 0.42]],
  marks: [{ name: "rug", kind: "spawn", at: [-0.6, -0.8], facing_deg: -20 }, { name: "sofa_seat", kind: "seat", at: [-0.3, 1.5], facing_deg: 0, item: "sofa_1" }],
  cameras: [{ name: "wide", at: [0, -2.8, 1.7], look_at: [0, 0.2, 0.9], lens_mm: 28 }],
};

describe("set geometry helpers", () => {
  it("faces south at 0° and east at 90°, like the contract", () => {
    const [x0, y0] = facing(0);
    const [x90, y90] = facing(90);
    expect([Math.round(x0), Math.round(y0)]).toEqual([0, -1]);
    expect([Math.round(x90), Math.round(y90) + 0]).toEqual([1, 0]);
  });

  it("sizes a prop with the contract's formula", () => {
    expect(propSize(TABLE)).toEqual([1.2, 0.8, 0.72]);
    const [w, d, h] = propSize(TABLE, { size: 1, width: 1 });
    expect([w, d, h].map((v) => Math.round(v * 1000) / 1000)).toEqual([1.8, 0.96, 0.864]);
  });

  it("starts an instance at its mark, moved and turned from it, or in front of a seat", () => {
    const inst = { ...blankInstance(1), mark: "rug", transform: { location: [0.1, 0, 0], rotation_deg: [0, 0, 5], scale: 1 } };
    const s = startOf(inst, LAYOUT);
    expect(s.at.map((v) => Math.round(v * 100) / 100)).toEqual([-0.5, -0.8]);
    expect(s.facing).toBe(-15);
    const seat = startOf({ ...inst, mark: "sofa_seat", transform: { location: [0, 0, 0] } }, LAYOUT);
    expect(seat.at[0]).toBeCloseTo(-0.3);
    expect(seat.at[1]).toBeCloseTo(1.5 - 0.425 - 0.27);
    expect(startOf({ ...inst, mark: null }, LAYOUT).at).toEqual([0.1, 0]);
  });

  it("drops marks, camera marks and empty prop rows when the scene has no environment", () => {
    const m = cleanManifest({ ...BLANK, name: "x", camera: { ...BLANK.camera, mark: "wide" },
      instances: [{ ...blankInstance(1), mark: "rug" }], props: [{ key: "p", prop: { code: "", version: "" }, at: [0, 0] }] });
    expect(m.environment).toBeNull();
    expect(m.camera.mark).toBeNull();
    expect(m.instances[0].mark).toBeNull();
    expect(m.props).toEqual([]);
    const withEnv = cleanManifest({ ...BLANK, name: "x", environment: { code: "ENV-001", version: "1.0" },
      camera: { ...BLANK.camera, mark: "wide" }, instances: [{ ...blankInstance(1), mark: "rug" }] });
    expect(withEnv.camera.mark).toBe("wide");
    expect(withEnv.instances[0].mark).toBe("rug");
  });
});

describe("SetPlan", () => {
  it("draws dressing, marks, cameras and the scene's own characters and props", () => {
    const { container } = render(<SetPlan layout={LAYOUT} highlight="camera:wide"
      dots={[{ key: "ali", at: [0, 0], facing_deg: 0, kind: "character" }, { key: "ball", at: [1, -1], kind: "prop" }]} />);
    expect(container.querySelectorAll(".solid")).toHaveLength(2);
    expect(container.querySelectorAll(".mark")).toHaveLength(2);
    expect(container.querySelector(".mark-seat")).toBeTruthy();
    expect(container.querySelector(".camera.on")).toBeTruthy();
    expect(container.querySelector(".cutaway")).toBeTruthy();
    expect([...container.querySelectorAll("text")].map((t) => t.textContent)).toEqual(["ali", "ball"]);
    // +Y is up on the plan: the sofa at y=1.5 is drawn at -1.5
    expect(container.querySelector(".solid")?.parentElement?.getAttribute("transform")).toBe("translate(-0.3 -1.5) rotate(0)");
  });
});

describe("PropSpecForm", () => {
  it("shows the size and surfaces a parameter change gives", () => {
    const qc = new QueryClient();
    qc.setQueryData(["prop-items"], [TABLE]);
    qc.setQueryData(["clothing", "family_cartoon@1.0"], { colors: { oak: "#B08850", royal_blue: "#2850B4" }, fit_profiles: [] });
    let spec: Record<string, unknown> = { item: { id: "PROPD-TABLE", version: "0.1" } };
    const onChange = vi.fn((s) => { spec = s; });
    const { rerender } = render(<QueryClientProvider client={qc}><PropSpecForm spec={spec} onChange={onChange} /></QueryClientProvider>);
    expect(screen.getByText(/1\.20 × 0\.80 × 0\.72 m/)).toBeTruthy();
    expect(screen.getByText(/top at 0\.72 m/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("size"), { target: { value: "1" } });
    rerender(<QueryClientProvider client={qc}><PropSpecForm spec={spec} onChange={onChange} /></QueryClientProvider>);
    expect(screen.getByText(/1\.44 × 0\.96 × 0\.86 m/)).toBeTruthy();
  });
});

describe("SceneSetEditor", () => {
  it("adds props that can stand on the environment's tops or on each other", () => {
    const qc = new QueryClient();
    qc.setQueryData(["lighting-presets"], [{ key: "night", label: "Night" }]);
    qc.setQueryData(["environments", { status: "published" }], { items: [], next_cursor: null });
    qc.setQueryData(["props", { status: "published" }], { items: [], next_cursor: null });
    let m = { camera: { preset: "wide" }, props: [{ key: "table", prop: { code: "PROP-002", version: "1.0" }, at: [1, 1] as [number, number] }] };
    const onChange = vi.fn((patch) => { m = { ...m, ...patch }; });
    render(<QueryClientProvider client={qc}><MemoryRouter>
      <SceneSetEditor m={m} instances={[]} onChange={onChange} /></MemoryRouter></QueryClientProvider>);
    expect(screen.getByLabelText("camera mark")).toHaveProperty("disabled", true);
    fireEvent.click(screen.getByText("Add prop"));
    expect(m.props).toHaveLength(2);
    expect(m.props[1].key).toBe("prop_2");
    fireEvent.change(screen.getByLabelText("lighting"), { target: { value: "night" } });
    expect(onChange).toHaveBeenLastCalledWith({ lighting: "night" });
  });
});
