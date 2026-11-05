// The visual storyboard (SRS 15): a timeline strip of the shots at their lengths, and a card
// per shot to edit the camera, placement, actions, lines, length and transition. Edits are
// kept locally and saved as a new storyboard; the server checks them against the script.
import type { ReactNode } from "react";
import { Field } from "./bits";

export interface ShotCamera { type: "wide" | "medium" | "close"; mark?: string | null; angle_deg?: number; focus?: string | null;
  movement: "static" | "push_in" | "pull_out" | "pan_left" | "pan_right" }
export interface ShotPlacement { character_id: string; mark?: string | null; at?: [number, number]; facing_deg?: number; emotion?: string | null }
export interface ShotAction { character_id: string; action: string; start_s: number; animation?: string | null; repeat?: number; travel?: boolean }
export interface ShotLine { character_id: string; text: string; start_s: number; emotion?: string | null; audio?: string | null }
export interface Transition { kind: "cut" | "fade" | "dissolve"; duration_s: number }
export interface Shot { shot_id: string; scene_id: string; description: string; duration_s: number; environment?: string | null;
  lighting?: string | null; camera: ShotCamera; placement: ShotPlacement[]; actions: ShotAction[]; dialogue: ShotLine[];
  transition_in: Transition }

export const MOVEMENTS: ShotCamera["movement"][] = ["static", "push_in", "pull_out", "pan_left", "pan_right"];
export const ACTIONS = ["idle", "stand", "wave", "walk", "nod", "point", "look_around", "look"];
export const EMOTIONS = ["neutral", "happy", "sad", "angry", "surprised", "fearful", "confused", "excited", "laughing", "crying"];

/** Where each shot starts: a dissolve overlaps the shot before by its length. */
export function timeline(shots: Pick<Shot, "duration_s" | "transition_in">[]): { start: number; end: number }[] {
  let t = 0;
  return shots.map((s, i) => {
    let start = t;
    if (i > 0 && s.transition_in.kind === "dissolve") start = Math.max(0, t - Math.min(s.transition_in.duration_s, s.duration_s));
    t = start + s.duration_s;
    return { start, end: t };
  });
}

export function totalSeconds(shots: Pick<Shot, "duration_s" | "transition_in">[]): number {
  const t = timeline(shots);
  return t.length ? t[t.length - 1].end : 0;
}

/** A character's display name from the series cast, else its code. */
export type Names = Record<string, string>;
const who = (names: Names, code: string) => names[code] ?? code;

export function StoryboardStrip({ shots, names, selected, onSelect }: { shots: Shot[]; names: Names; selected?: number;
  onSelect?: (i: number) => void }) {
  const t = timeline(shots);
  const total = totalSeconds(shots) || 1;
  return (
    <div className="sb-strip" role="list" aria-label="storyboard timeline">
      {shots.map((s, i) => (
        <button key={s.shot_id} role="listitem" type="button" onClick={() => onSelect?.(i)}
          className={`sb-shot sb-${s.camera.type}${selected === i ? " sb-selected" : ""}`}
          style={{ left: `${(t[i].start / total) * 100}%`, width: `${(s.duration_s / total) * 100}%` }}
          title={`${s.shot_id} · ${s.duration_s.toFixed(1)} s · ${s.camera.type}${s.camera.movement !== "static" ? ` ${s.camera.movement}` : ""}`}>
          <strong>{s.shot_id}</strong>
          <small>{s.camera.type}{s.dialogue[0] ? ` · ${who(names, s.dialogue[0].character_id)}` : ""}</small>
          {s.transition_in.kind !== "cut" && <span className="sb-tr">{s.transition_in.kind}</span>}
        </button>
      ))}
    </div>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="row">{children}</div>;
}

export function ShotCard({ shot, names, marks, cameras, onChange, readOnly }: { shot: Shot; names: Names; marks: string[];
  cameras: string[]; onChange: (s: Shot) => void; readOnly?: boolean }) {
  const set = (patch: Partial<Shot>) => onChange({ ...shot, ...patch });
  const cam = (patch: Partial<ShotCamera>) => set({ camera: { ...shot.camera, ...patch } });
  const cast = shot.placement.map((p) => p.character_id);
  return (
    <fieldset className="panel shot-card" disabled={readOnly} aria-label={`shot ${shot.shot_id}`}>
      <div className="panel-head"><h3>{shot.shot_id}</h3><small>{shot.scene_id}{shot.environment ? ` · ${shot.environment}` : ""}</small></div>
      <Field label="Description">
        <input value={shot.description} maxLength={2000} onChange={(e) => set({ description: e.target.value })} aria-label="description" />
      </Field>
      <Row>
        <Field label="Length (s)">
          <input type="number" min={0.5} max={30} step={0.1} value={shot.duration_s} aria-label="length"
            onChange={(e) => set({ duration_s: Number(e.target.value) })} />
        </Field>
        <Field label="Camera">
          <select value={shot.camera.type} aria-label="camera" onChange={(e) => cam({ type: e.target.value as ShotCamera["type"] })}>
            <option value="wide">wide</option><option value="medium">medium</option><option value="close">close</option>
          </select>
        </Field>
        <Field label="Movement">
          <select value={shot.camera.movement} aria-label="movement" onChange={(e) => cam({ movement: e.target.value as ShotCamera["movement"] })}>
            {MOVEMENTS.map((m) => <option key={m} value={m}>{m.replace("_", " ")}</option>)}
          </select>
        </Field>
        <Field label="Camera mark">
          <select value={shot.camera.mark ?? ""} aria-label="camera mark" onChange={(e) => cam({ mark: e.target.value || null })}>
            <option value="">framed on the cast</option>
            {cameras.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="Focus">
          <select value={shot.camera.focus ?? ""} aria-label="focus" onChange={(e) => cam({ focus: e.target.value || null })}>
            <option value="">everyone</option>
            {cast.map((c) => <option key={c} value={c}>{who(names, c)}</option>)}
          </select>
        </Field>
        <Field label="Comes in by">
          <select value={shot.transition_in.kind} aria-label="transition" onChange={(e) => {
            const kind = e.target.value as Transition["kind"];
            set({ transition_in: { kind, duration_s: kind === "cut" ? 0 : shot.transition_in.duration_s || 0.5 } });
          }}>
            <option value="cut">cut</option><option value="fade">fade from black</option><option value="dissolve">dissolve</option>
          </select>
        </Field>
      </Row>
      <div>
        <strong>Placement</strong>
        {shot.placement.map((p, i) => (
          <Row key={p.character_id}>
            <span className="grow">{who(names, p.character_id)}</span>
            <Field label="Mark">
              <select value={p.mark ?? ""} aria-label={`${p.character_id} mark`} onChange={(e) => set({ placement: shot.placement.map((q, j) =>
                j === i ? { ...q, mark: e.target.value || null } : q) })}>
                <option value="">free</option>
                {marks.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </Field>
            <Field label="Expression">
              <select value={p.emotion ?? ""} aria-label={`${p.character_id} expression`} onChange={(e) => set({ placement: shot.placement.map((q, j) =>
                j === i ? { ...q, emotion: e.target.value || null } : q) })}>
                <option value="">neutral</option>
                {EMOTIONS.filter((x) => x !== "neutral").map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </Field>
          </Row>
        ))}
      </div>
      <div>
        <strong>Actions</strong>
        {shot.actions.map((a, i) => (
          <Row key={i}>
            <span>{who(names, a.character_id)}</span>
            <select value={a.action} aria-label={`action ${i + 1}`} onChange={(e) => set({ actions: shot.actions.map((q, j) =>
              j === i ? { ...q, action: e.target.value } : q) })}>
              {[...new Set([a.action, ...ACTIONS])].map((x) => <option key={x} value={x}>{x}</option>)}
            </select>
            <Field label="at (s)">
              <input type="number" min={0} max={30} step={0.1} value={a.start_s} aria-label={`action ${i + 1} start`}
                onChange={(e) => set({ actions: shot.actions.map((q, j) => (j === i ? { ...q, start_s: Number(e.target.value) } : q)) })} />
            </Field>
            {!readOnly && <button type="button" onClick={() => set({ actions: shot.actions.filter((_, j) => j !== i) })}>remove</button>}
          </Row>
        ))}
        {!readOnly && cast.length > 0 && (
          <button type="button" onClick={() => set({ actions: [...shot.actions, { character_id: cast[0], action: "wave", start_s: 0 }] })}>
            Add action</button>
        )}
      </div>
      {shot.dialogue.length > 0 && (
        <div>
          <strong>Lines</strong>
          {shot.dialogue.map((ln, i) => (
            <Row key={i}>
              <span>{who(names, ln.character_id)}:</span>
              <span className="grow">“{ln.text}”</span>
              <Field label="at (s)">
                <input type="number" min={0} max={30} step={0.1} value={ln.start_s} aria-label={`line ${i + 1} start`}
                  onChange={(e) => set({ dialogue: shot.dialogue.map((q, j) => (j === i ? { ...q, start_s: Number(e.target.value) } : q)) })} />
              </Field>
              {ln.audio ? <span className="chip chip-ready">{ln.audio}</span> : <span className="chip">not voiced</span>}
            </Row>
          ))}
        </div>
      )}
    </fieldset>
  );
}
