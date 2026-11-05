import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AudioOut } from "../api/client";
import { AnimationSpecForm } from "../components/AnimationSpecForm";
import { Instance, InstanceEditor } from "../components/InstanceEditor";
import { CueStrip } from "../pages/AudioPage";
import { blankInstance } from "../pages/SceneEditorPage";

afterEach(cleanup);

const GENS = [
  { id: "GEN-WALK", version: "1.0", key: "walk", label: "Walk cycle", category: "locomotion", frames: 28, loops: true, travels: true,
    params: { cadence: { min: 70, max: 140, default: 100, description: "steps per minute" } }, needs_expression: false },
  { id: "GEN-EXPR", version: "1.0", key: "expression", label: "Expression take", category: "expression", frames: 48, loops: false,
    travels: false, params: { intensity: { min: 0, max: 1, default: 1, description: "how strong" } }, needs_expression: true },
];

function client() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(["animation-generators"], GENS);
  qc.setQueryData(["bone-maps"], [{ id: "cmu", label: "CMU mocap", target_rig_profile: "cartoon_humanoid@1.0", root: "Hips", bones: {}, passthrough: [] }]);
  qc.setQueryData(["clothing", "family_cartoon@1.0"], { fit_profiles: ["child_average", "adult_average"], colors: {} });
  return qc;
}

describe("AnimationSpecForm", () => {
  it("picks a generator, sets its parameters, and asks for an expression only when it needs one", () => {
    let spec: Record<string, unknown> = { source: { kind: "generator", generator: { id: "GEN-WALK", version: "1.0" } } };
    const onChange = vi.fn((s) => { spec = s; });
    const view = () => <QueryClientProvider client={client()}><AnimationSpecForm spec={spec} onChange={onChange} /></QueryClientProvider>;
    const { rerender } = render(view());
    expect(screen.getByText(/28 frames at 24 fps, loops, travels forward/)).toBeTruthy();
    expect(screen.queryByLabelText("expression")).toBeNull();
    fireEvent.change(screen.getByLabelText("cadence"), { target: { value: "120" } });
    expect((spec.source as any).params).toEqual({ cadence: 120 }); // eslint-disable-line @typescript-eslint/no-explicit-any
    fireEvent.change(screen.getByLabelText("generator"), { target: { value: "GEN-EXPR" } });
    expect((spec.source as any).generator).toEqual({ id: "GEN-EXPR", version: "1.0" }); // eslint-disable-line @typescript-eslint/no-explicit-any
    rerender(view());
    fireEvent.change(screen.getByLabelText("expression"), { target: { value: "happy" } });
    expect((spec.source as any).expression).toBe("happy"); // eslint-disable-line @typescript-eslint/no-explicit-any
    // the first ticked profile is the reference body
    fireEvent.click(screen.getByLabelText("adult average"));
    expect(spec.fit_profiles).toEqual(["adult_average"]);
  });

  it("switches to a motion-capture take with its skeleton, travel and licence", () => {
    let spec: Record<string, unknown> = { source: { kind: "generator", generator: { id: "GEN-WALK", version: "1.0" } } };
    const onChange = vi.fn((s) => { spec = s; });
    const view = () => <QueryClientProvider client={client()}><AnimationSpecForm spec={spec} onChange={onChange} /></QueryClientProvider>;
    const { rerender } = render(view());
    fireEvent.change(screen.getByLabelText("source"), { target: { value: "bvh" } });
    expect(spec).toMatchObject({ category: "locomotion", source: { kind: "bvh", skeleton: "cmu" } });
    rerender(view());
    expect(screen.getByLabelText("BVH file")).toBeTruthy();
    fireEvent.click(screen.getByText(/moves through space/).querySelector("input")!);
    fireEvent.change(screen.getByLabelText("licence"), { target: { value: "CMU mocap" } });
    expect(spec.source).toMatchObject({ kind: "bvh", licence: "CMU mocap" });
  });
});

describe("CueStrip", () => {
  it("draws each mouth cue across the clip's length", () => {
    const clip = { code: "AUDIO-001", duration_s: 2, cues: [
      { start_s: 0, end_s: 0.5, viseme: "rest", shape: "rest" }, { start_s: 0.5, end_s: 1.5, viseme: "AI", shape: "AI" },
      { start_s: 1.5, end_s: 2, viseme: "rest", shape: "rest" }] } as unknown as AudioOut;
    const { container } = render(<CueStrip clip={clip} />);
    const cues = [...container.querySelectorAll<HTMLElement>(".cue")];
    expect(cues.map((c) => [c.style.left, c.style.width, c.textContent])).toEqual([["0%", "25%", ""], ["25%", "50%", "AI"], ["75%", "25%", ""]]);
  });
});

describe("InstanceEditor with the animation library and recordings", () => {
  it("offers published animations, their travel switch, and a recording per line", () => {
    const qc = client();
    qc.setQueryData(["clips"], [{ id: "CLIP-IDLE", version: "0.1", key: "idle", label: "Idle breathing", frames: 48, loops: true, controls: [] }]);
    qc.setQueryData(["characters", { status: "published" }], { items: [], next_cursor: null });
    qc.setQueryData(["animations", { status: "published" }], { items: [
      { id: "a1", code: "ANIM-001", name: "Walk", category: "locomotion", latest_published: "1.0", tags: [] },
      { id: "a2", code: "ANIM-002", name: "Draft only", category: "gesture", latest_published: null, tags: [] }], next_cursor: null });
    qc.setQueryData(["audio-clips", { status: "ready" }], { items: [
      { id: "c1", code: "AUDIO-001", name: "hello.wav", status: "ready", duration_s: 4.3, dialog_text: "Hello there!" }], next_cursor: null });
    let inst: Instance = { ...blankInstance(1), animations: [{ clip: "CLIP-IDLE@0.1", start_frame: 1 }],
      dialogue: [{ start_frame: 1, end_frame: 24, text: "" }] };
    const onChange = vi.fn((v: Instance) => { inst = v; });
    const view = () => <QueryClientProvider client={qc}><MemoryRouter>
      <InstanceEditor inst={inst} frameEnd={120} onChange={onChange} onRemove={() => {}} /></MemoryRouter></QueryClientProvider>;
    const { rerender } = render(view());
    expect(screen.queryByText(/Draft only/)).toBeNull();
    fireEvent.change(screen.getByLabelText("clip"), { target: { value: "ANIM-001@1.0" } });
    expect(inst.animations?.[0].clip).toBe("ANIM-001@1.0");
    rerender(view());
    fireEvent.click(screen.getByText("travel").querySelector("input")!);
    expect(inst.animations?.[0].travel).toBe(false);
    fireEvent.change(screen.getByLabelText("recording"), { target: { value: "AUDIO-001" } });
    expect(inst.dialogue?.[0]).toEqual({ start_frame: 1, end_frame: 24, text: "Hello there!", audio: "AUDIO-001" });
  });
});
