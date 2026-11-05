import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChecksReport, detailOf } from "../components/ChecksReport";
import { Instance, InstanceEditor, parseState, PinChips, stateText } from "../components/InstanceEditor";
import { BLANK, blankInstance, cleanManifest } from "../pages/SceneEditorPage";

afterEach(cleanup);

describe("scene manifest helpers", () => {
  it("parses free-form state into typed values and back", () => {
    const s = parseState("mood=cheerful, holding=ball\nlevel=3, wet=false");
    expect(s).toEqual({ mood: "cheerful", holding: "ball", level: 3, wet: false });
    expect(stateText(s)).toBe("mood=cheerful, holding=ball, level=3, wet=false");
  });

  it("drops empty accessory rows and an unset focus before sending", () => {
    const inst = { ...blankInstance(1), accessories: [{ accessory: { code: "", version: "" } }, { accessory: { code: "ACC-001", version: "1.0" } }] };
    const m = cleanManifest({ ...BLANK, name: "x", instances: [inst], camera: { ...BLANK.camera, focus: "" } });
    expect(m.instances[0].accessories).toEqual([{ accessory: { code: "ACC-001", version: "1.0" } }]);
    expect(m.camera.focus).toBeNull();
  });
});

describe("ChecksReport", () => {
  it("lists every check with its verdict and opens when something failed", () => {
    const report = { ok: false, failed: ["ali:expressions_play"], checks: [
      { name: "ali:placement", ok: true, location: [0, 0, 0] }, { name: "ali:expressions_play", ok: false, mismatches: [] }] };
    const { container } = render(<ChecksReport title="Reopen check" report={report} />);
    expect(screen.getByText(/Reopen check: failed \(1\/2 checks\)/)).toBeTruthy();
    expect(container.querySelector("details")?.open).toBe(true);
    expect([...container.querySelectorAll("li")].map((l) => l.className)).toEqual(["ok", "bad"]);
    expect(detailOf(report.checks[0])).toBe('{"location":[0,0,0]}');
  });
});

describe("PinChips", () => {
  it("shows each instance's exact pins and a director override", () => {
    const resolved = { instances: [{ key: "ali_school", character: { name: "Ali", code: "CHAR-001", version: "1.0" },
      outfit: { name: "School", code: "OUTFIT-001", version: "1.0" }, outfit_compatibility: { status: "verified", override: true },
      accessories: [{ pin: { code: "ACC-001", version: "1.0" }, attach_point: "face_eyes" }], animations: [{ key: "wave" }] }] };
    render(<PinChips resolved={resolved} />);
    expect(screen.getByText("CHAR-001@1.0")).toBeTruthy();
    expect(screen.getByText("director override")).toBeTruthy();
    expect(screen.getByText(/on face_eyes/)).toBeTruthy();
  });
});

describe("InstanceEditor", () => {
  it("edits placement, expressions and clips of one instance", () => {
    const qc = new QueryClient();
    qc.setQueryData(["clips"], [{ id: "CLIP-WAVE", version: "0.1", key: "wave", label: "Wave (right hand)", frames: 36, loops: false, controls: [] },
      { id: "CLIP-IDLE", version: "0.1", key: "idle", label: "Idle breathing", frames: 48, loops: true, controls: [] }]);
    qc.setQueryData(["characters", { status: "published" }], { items: [], next_cursor: null });
    let inst: Instance = blankInstance(1);
    const onChange = vi.fn((v: Instance) => { inst = v; });
    const { rerender } = render(<QueryClientProvider client={qc}><MemoryRouter>
      <InstanceEditor inst={inst} frameEnd={48} onChange={onChange} onRemove={() => {}} /></MemoryRouter></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("x (m)"), { target: { value: "0.5" } });
    expect(inst.transform?.location).toEqual([0.5, 0, 0]);
    fireEvent.click(screen.getByText("Add expression key"));
    expect(inst.expressions).toEqual([{ frame: 1, expression: "happy", weight: 1 }]);
    rerender(<QueryClientProvider client={qc}><MemoryRouter>
      <InstanceEditor inst={inst} frameEnd={48} onChange={onChange} onRemove={() => {}} /></MemoryRouter></QueryClientProvider>);
    fireEvent.change(screen.getByLabelText("clip"), { target: { value: "CLIP-WAVE@0.1" } });
    expect(inst.animations?.[0].clip).toBe("CLIP-WAVE@0.1");
  });
});
