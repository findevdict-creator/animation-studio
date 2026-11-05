import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { JobOut, RenderOut } from "../api/client";
import { RenderPanel, renderSummary } from "../components/RenderPanel";
import { type Shot, ShotCard, StoryboardStrip, timeline, totalSeconds } from "../components/Storyboard";
import { blankMember, cleanScript, memberBody } from "../pages/EpisodePages";

afterEach(cleanup);

const shot = (n: number, patch: Partial<Shot> = {}): Shot => ({
  shot_id: `SH-00${n}`, scene_id: "SC-001", description: `shot ${n}`, duration_s: 2, environment: "ENV-001",
  camera: { type: "wide", movement: "static", mark: null, focus: null },
  placement: [{ character_id: "CHAR-001", mark: "spawn_a" }, { character_id: "CHAR-002", mark: "spawn_b" }],
  actions: [], dialogue: [], transition_in: { kind: "cut", duration_s: 0 }, ...patch });
const names = { "CHAR-001": "Ali", "CHAR-002": "Maya" };

describe("storyboard timeline", () => {
  it("lays shots end to end and lets a dissolve overlap the shot before", () => {
    const shots = [shot(1), shot(2, { duration_s: 3 }), shot(3, { transition_in: { kind: "dissolve", duration_s: 0.5 } })];
    expect(timeline(shots)).toEqual([{ start: 0, end: 2 }, { start: 2, end: 5 }, { start: 4.5, end: 6.5 }]);
    expect(totalSeconds(shots)).toBe(6.5);
    expect(totalSeconds([])).toBe(0);
  });

  it("draws each shot at its length and selects one", () => {
    const onSelect = vi.fn();
    const shots = [shot(1), shot(2, { camera: { type: "close", movement: "push_in", focus: "CHAR-002" },
      dialogue: [{ character_id: "CHAR-002", text: "Hi", start_s: 0.3 }], transition_in: { kind: "fade", duration_s: 0.5 } })];
    render(<StoryboardStrip shots={shots} names={names} selected={0} onSelect={onSelect} />);
    const items = screen.getAllByRole("listitem");
    expect(items.map((i) => (i as HTMLElement).style.width)).toEqual(["50%", "50%"]);
    expect(items[0].className).toContain("sb-selected");
    expect(items[1].className).toContain("sb-close");
    expect(screen.getByText("close · Maya")).toBeTruthy();
    expect(screen.getByText("fade")).toBeTruthy();
    fireEvent.click(items[1]);
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});

describe("ShotCard", () => {
  it("edits the camera, the placement, the actions and the transition", () => {
    let s = shot(1, { dialogue: [{ character_id: "CHAR-001", text: "Hello Maya", start_s: 0.3, audio: "AUDIO-001" }] });
    const onChange = vi.fn((v: Shot) => { s = v; });
    const view = () => <ShotCard shot={s} names={names} marks={["spawn_a", "spawn_b"]} cameras={["cam_wide"]} onChange={onChange} />;
    const { rerender } = render(view());
    fireEvent.change(screen.getByLabelText("camera"), { target: { value: "medium" } });
    expect(s.camera.type).toBe("medium");
    rerender(view());
    fireEvent.change(screen.getByLabelText("camera mark"), { target: { value: "cam_wide" } });
    rerender(view());
    fireEvent.change(screen.getByLabelText("focus"), { target: { value: "CHAR-001" } });
    expect(s.camera).toMatchObject({ type: "medium", mark: "cam_wide", focus: "CHAR-001" });
    rerender(view());
    fireEvent.change(screen.getByLabelText("CHAR-002 expression"), { target: { value: "happy" } });
    expect(s.placement[1]).toMatchObject({ mark: "spawn_b", emotion: "happy" });
    rerender(view());
    fireEvent.click(screen.getByText("Add action"));
    expect(s.actions).toEqual([{ character_id: "CHAR-001", action: "wave", start_s: 0 }]);
    rerender(view());
    fireEvent.change(screen.getByLabelText("transition"), { target: { value: "dissolve" } });
    expect(s.transition_in).toEqual({ kind: "dissolve", duration_s: 0.5 });
    rerender(view());
    fireEvent.change(screen.getByLabelText("length"), { target: { value: "3.5" } });
    expect(s.duration_s).toBe(3.5);
    expect(screen.getByText("AUDIO-001")).toBeTruthy();
    expect(screen.getByText("“Hello Maya”")).toBeTruthy();
  });

  it("cannot be edited when read only", () => {
    render(<ShotCard shot={shot(1)} names={names} marks={[]} cameras={[]} onChange={() => {}} readOnly />);
    expect((screen.getByLabelText("shot SH-001") as HTMLFieldSetElement).disabled).toBe(true);
    expect(screen.queryByText("Add action")).toBeNull();
  });
});

describe("episode page helpers", () => {
  it("renumbers scenes and drops empty lines before saving a script", () => {
    const s = cleanScript({ title: "T", synopsis: "", scenes: [
      { scene_id: "SC-009", location_id: "", description: "a", characters: ["CHAR-001"], events: [],
        dialogue: [{ character_id: "CHAR-001", text: "  " }, { character_id: "CHAR-001", text: "Hi", emotion: "" }] }] });
    expect(s.scenes[0]).toMatchObject({ scene_id: "SC-001", location_id: null, dialogue: [{ text: "Hi", emotion: null }] });
  });

  it("turns a cast row into the API's member with a pinned outfit and a voice", () => {
    const m = memberBody({ ...blankMember(), character: "CHAR-001", outfit: "OUTFIT-002@1.1", variant: "m3", pitch: 40 });
    expect(m).toEqual({ character: "CHAR-001", key: "char_001", role: "main", outfit_version: { code: "OUTFIT-002", version: "1.1" },
      voice: { provider: "espeak-ng", voice: "en-us", variant: "m3", pitch: 40, rate_wpm: 160 } });
    expect(memberBody({ ...blankMember(), character: "CHAR-002", key: "maya" }).outfit_version).toBeNull();
  });
});

const job = (patch: Partial<JobOut>): JobOut => ({ id: "j1", type: "render_scene", status: "succeeded", progress: 1,
  progress_message: "", ...patch } as JobOut);
const settings = { width: 1920, height: 1080, fps: 24, engine: "CYCLES", samples: 12, view_transform: "Standard",
  video_codec: "h264", crf: 18, audio_codec: "aac", audio_bitrate_k: 192 };
const rjob = (patch: Partial<RenderOut>): RenderOut => ({
  id: "r1", code: "RENDER-001", episode_id: "e", episode_version_id: "v", status: "rendering",
  progress: { value: 0.45, shots_done: 1, shots: 2, frames: 48 }, settings, asset_versions: ["CHAR-001@1.0", "OUTFIT-001@1.0"],
  blender_version: "5.0.1", output_location: null, error: null, cancel_requested: false, created_at: "2026-11-05T10:00:00Z",
  started_at: null, finished_at: null, export_job: null, outputs: [], video_url: null,
  shots: [{ shot_id: "SH-001", scene_version_id: "s1", frames: 48, job: job({ id: "j1" }) },
    { shot_id: "SH-002", scene_version_id: "s2", frames: null, job: job({ id: "j2", status: "running", progress: 0.3, progress_message: "frame 7/48" }) }],
  ...patch } as RenderOut);

function wrap(children: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider>;
}

describe("RenderPanel", () => {
  it("shows a running render's progress, settings, pins and shot jobs, with cancel", () => {
    render(wrap(<RenderPanel render={rjob({})} canRun />));
    expect(renderSummary(rjob({}))).toBe("1920×1080 · 24 fps · CYCLES 12 samples · Standard · h264 crf 18 + aac 192 kb/s");
    expect((screen.getByLabelText("render progress") as HTMLProgressElement).value).toBe(0.45);
    expect(screen.getByText("1/2 shots rendered · 48 frames")).toBeTruthy();
    expect(screen.getByText("CHAR-001@1.0")).toBeTruthy();
    expect(screen.getByText(/30% frame 7\/48/)).toBeTruthy();
    expect(screen.getByText("Cancel")).toBeTruthy();
    expect(screen.queryByText("Retry failed jobs")).toBeNull();
    expect(screen.queryByLabelText("episode video")).toBeNull();
  });

  it("shows the finished video and offers a retry only when failed", () => {
    const done = rjob({ status: "completed", progress: { value: 1, shots_done: 2, shots: 2, frames: 96 }, video_url: "https://x/ep.mp4",
      export_job: job({ id: "j3", type: "export_episode" }), outputs: [{ id: "o", kind: "video", shot_id: null, scene_version_id: null,
        storage_key: "k", sha256: "s", size_bytes: 2_000_000, frame_count: 96, meta: { duration_s: 4, video: { frames: 96 }, audio: { codec: "aac" } } }] });
    const { unmount } = render(wrap(<RenderPanel render={done} canRun />));
    expect((screen.getByLabelText("episode video") as HTMLVideoElement).src).toBe("https://x/ep.mp4");
    expect(screen.getByText("download MP4")).toBeTruthy();
    expect(screen.queryByText("Cancel")).toBeNull();
    expect(screen.queryByLabelText("render progress")).toBeNull();
    unmount();
    render(wrap(<RenderPanel render={rjob({ status: "failed", error: { message: "shot SH-002 failed" } })} canRun />));
    expect(screen.getByText("Retry failed jobs")).toBeTruthy();
    expect(screen.getByText("shot SH-002 failed")).toBeTruthy();
  });
});
