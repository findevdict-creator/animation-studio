// Structured controls for an EnvironmentSpecification: the template, its size within the
// template's range, floor and wall materials, the lighting preset, set dressing (the
// template's own, or an explicit list of catalog props), named marks and camera marks. Every
// change is checked by the server's validator (the same one used on save and generate), and
// the plan shows the layout it resolves to, with any placement issue listed beside it.
import { useEffect, useState } from "react";
import { api, S } from "../api/client";
import { useClothing, useEnvironmentTemplates, useLightingPresets, usePropItems } from "../api/hooks";
import { Field } from "./bits";
import { asLayout, SetPlan } from "./SetPlan";

type Spec = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Dress = { id?: string; prop: { id: string; version: string }; at: [number, number]; rotation_deg?: number; on?: string | null };
type MarkRow = { name: string; kind: "spawn" | "seat" | "interaction"; at: [number, number]; facing_deg: number; item?: string | null };
type CamRow = { name: string; at: [number, number, number]; look_at: [number, number, number]; lens_mm: number };

const num = (v: string, d = 0) => (v !== "" && Number.isFinite(Number(v)) ? Number(v) : d);
const round = (v: number) => Math.round(v * 100) / 100;

/** Validate as the user edits (debounced); the answer carries the resolved layout. */
export function useEnvironmentCheck(spec: Spec) {
  const [out, setOut] = useState<S["EnvironmentValidationOut"]>();
  const body = JSON.stringify(spec);
  useEffect(() => {
    if (!spec.template?.id) return;
    let live = true;
    const t = setTimeout(() => {
      api<S["EnvironmentValidationOut"]>("/environments/spec/validate", { method: "POST", json: { name: "check", ...spec } })
        .then((r) => live && setOut(r)).catch(() => undefined);
    }, 350);
    return () => { live = false; clearTimeout(t); };
  }, [body]); // eslint-disable-line react-hooks/exhaustive-deps
  return out;
}

export function EnvironmentSpecForm({ spec, onChange, lockTemplate }: { spec: Spec; onChange: (s: Spec) => void; lockTemplate?: boolean }) {
  const templates = useEnvironmentTemplates();
  const lighting = useLightingPresets();
  const props = usePropItems();
  const clothing = useClothing();
  const check = useEnvironmentCheck(spec);
  const t = templates.data?.find((x) => x.id === spec.template?.id);
  const colors = clothing.data?.colors ?? {};
  if (!templates.data || !props.data) return <p>loading the environment templates…</p>;
  const size: [number, number, number] = spec.size ?? t?.default_size ?? [4, 4, 2.6];
  const mats = spec.materials ?? {};
  const setMat = (k: string, v: string | undefined) => onChange({ ...spec, materials: { ...mats, [k]: v } });
  const dressing: Dress[] | null = spec.dressing ?? null;
  const marks: MarkRow[] = spec.marks ?? [];
  const cams: CamRow[] = spec.cameras ?? [];
  const putList = <T,>(key: string, list: T[], i: number, v: T | null) => {
    const next = [...list];
    if (v === null) next.splice(i, 1); else next[i] = v;
    onChange({ ...spec, [key]: next });
  };
  const resolvedDressing = (check?.resolved?.dressing ?? []) as S["ResolvedDressing"][];
  const explicit = (): Dress[] => resolvedDressing.map((d) => {
    const [id, version] = d.prop.item.split("@");
    return { id: d.id, prop: { id, version }, at: [round(d.at[0]), round(d.at[1])], rotation_deg: d.rotation_deg, on: d.on };
  });
  const layout = asLayout(check?.layout);
  const surfaces = (check?.layout as { tops?: [string][]; seats?: [string][] } | undefined);
  const topIds = (surfaces?.tops ?? []).map((x) => x[0]);
  const seatIds = (surfaces?.seats ?? []).map((x) => x[0]);
  const lightingKeys = (lighting.data ?? []).map((l) => l.key);

  return (
    <fieldset className="spec-form environment-form">
      <legend>Environment</legend>
      <div className="grid2">
        <div>
          <Field label="Template">
            <select value={spec.template?.id ?? ""} disabled={lockTemplate} aria-label="template"
              onChange={(e) => {
                const next = templates.data.find((x) => x.id === e.target.value);
                if (next) onChange({ ...spec, template: { id: next.id, version: next.version }, size: undefined, materials: {},
                  lighting: undefined, dressing: undefined, openings: undefined, marks: [], cameras: [] });
              }}>
              <option value="" disabled>choose…</option>
              {templates.data.map((x) => <option key={x.id} value={x.id}>{x.label} ({x.kind})</option>)}
            </select>
          </Field>
          {t && (
            <>
              <div className="row">
                {["width", "depth", ...(t.kind === "room" ? ["wall height"] : [])].map((label, i) => (
                  <Field key={label} label={`${label} (m)`}>
                    <input type="number" step={0.1} min={t.min_size[i]} max={t.max_size[i]} value={size[i]} aria-label={label}
                      onChange={(e) => onChange({ ...spec, size: size.map((v, j) => (j === i ? num(e.target.value, v) : v)) })} />
                  </Field>
                ))}
              </div>
              <div className="row">
                <Field label="Floor">
                  <select value={mats.floor_finish ?? ""} onChange={(e) => setMat("floor_finish", e.target.value || undefined)} aria-label="floor finish">
                    <option value="">{t.default_floor} (default)</option>
                    {t.floor_finishes.map((f) => <option key={f}>{f}</option>)}
                  </select>
                </Field>
                <Field label="Floor colour">
                  <select value={mats.floor_color ?? ""} onChange={(e) => setMat("floor_color", e.target.value || undefined)} aria-label="floor colour">
                    <option value="">default</option>
                    {Object.keys(colors).map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
                  </select>
                </Field>
                {t.kind === "room" && (
                  <>
                    <Field label="Walls">
                      <select value={mats.wall_finish ?? ""} onChange={(e) => setMat("wall_finish", e.target.value || undefined)} aria-label="wall finish">
                        <option value="">{t.default_wall} (default)</option>
                        {t.wall_finishes.map((f) => <option key={f}>{f}</option>)}
                      </select>
                    </Field>
                    <Field label="Wall colour">
                      <select value={mats.wall_color ?? ""} onChange={(e) => setMat("wall_color", e.target.value || undefined)} aria-label="wall colour">
                        <option value="">default</option>
                        {Object.keys(colors).map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
                      </select>
                    </Field>
                  </>
                )}
                <Field label="Lighting">
                  <select value={spec.lighting ?? ""} onChange={(e) => onChange({ ...spec, lighting: e.target.value || undefined })} aria-label="lighting">
                    <option value="">{t.default_lighting} (template default)</option>
                    {lightingKeys.map((k) => <option key={k} value={k}>{lighting.data?.find((l) => l.key === k)?.label ?? k}</option>)}
                  </select>
                </Field>
              </div>
            </>
          )}
        </div>
        <div>
          {layout && <SetPlan layout={layout} label="environment plan" />}
          {check && check.issues.length > 0 && (
            <ul className="issues" aria-label="layout issues">
              {check.issues.map((x, i) => <li key={i} className={x.severity}>{x.path}: {x.message}</li>)}
            </ul>
          )}
          {check?.valid && <p className="hint">The layout checks out: nothing overlaps, every mark and the door are reachable.</p>}
        </div>
      </div>

      {t && (
        <>
          <h4>Set dressing</h4>
          {dressing === null ? (
            <p className="hint">
              Using the template's own dressing ({resolvedDressing.length} items).{" "}
              <button type="button" disabled={!resolvedDressing.length} onClick={() => onChange({ ...spec, dressing: explicit() })}>Edit the items</button>{" "}
              <button type="button" onClick={() => onChange({ ...spec, dressing: [] })}>Start empty</button>
            </p>
          ) : (
            <>
              {dressing.map((d, i) => (
                <div className="row" key={i}>
                  <input value={d.id ?? ""} placeholder="id" aria-label="dressing id" pattern="[a-z][a-z0-9_]{0,31}"
                    onChange={(e) => putList("dressing", dressing, i, { ...d, id: e.target.value || undefined })} />
                  <select value={d.prop.id} aria-label="dressing item"
                    onChange={(e) => { const p = props.data.find((x) => x.id === e.target.value)!; putList("dressing", dressing, i, { ...d, prop: { id: p.id, version: p.version } }); }}>
                    {props.data.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                  </select>
                  {(["x", "y"] as const).map((a, j) => (
                    <input key={a} type="number" step={0.05} aria-label={`dressing ${a}`} value={d.at[j]}
                      onChange={(e) => putList("dressing", dressing, i, { ...d, at: d.at.map((v, k) => (k === j ? num(e.target.value) : v)) as [number, number] })} />
                  ))}
                  <input type="number" step={15} aria-label="dressing turn" value={d.rotation_deg ?? 0}
                    onChange={(e) => putList("dressing", dressing, i, { ...d, rotation_deg: num(e.target.value) })} />
                  <select value={d.on ?? ""} aria-label="dressing on" onChange={(e) => putList("dressing", dressing, i, { ...d, on: e.target.value || null })}>
                    <option value="">on the floor</option>
                    {topIds.filter((x) => x !== d.id).map((x) => <option key={x} value={x}>on {x}</option>)}
                  </select>
                  <button type="button" aria-label="remove dressing" onClick={() => putList("dressing", dressing, i, null)}>✕</button>
                </div>
              ))}
              <div className="buttons">
                <button type="button" disabled={dressing.length >= 60}
                  onClick={() => putList("dressing", dressing, dressing.length, { prop: { id: props.data[0].id, version: props.data[0].version }, at: [0, 0] })}>Add item</button>
                <button type="button" onClick={() => onChange({ ...spec, dressing: undefined })}>Use the template's dressing</button>
              </div>
            </>
          )}

          <h4>Marks <small>(with none, there is one "centre" mark; a "door" mark is added when the room has a door)</small></h4>
          {marks.map((m, i) => (
            <div className="row" key={i}>
              <input value={m.name} aria-label="mark name" pattern="[a-z][a-z0-9_]{0,31}" onChange={(e) => putList("marks", marks, i, { ...m, name: e.target.value })} />
              <select value={m.kind} aria-label="mark kind" onChange={(e) => putList("marks", marks, i, { ...m, kind: e.target.value as MarkRow["kind"] })}>
                {["spawn", "seat", "interaction"].map((k) => <option key={k}>{k}</option>)}
              </select>
              {m.kind === "seat" ? (
                <select value={m.item ?? ""} aria-label="mark seat" onChange={(e) => putList("marks", marks, i, { ...m, item: e.target.value || null })}>
                  <option value="" disabled>which seat…</option>
                  {seatIds.map((x) => <option key={x} value={x}>{x}</option>)}
                </select>
              ) : (["x", "y"] as const).map((a, j) => (
                <input key={a} type="number" step={0.05} aria-label={`mark ${a}`} value={m.at[j]}
                  onChange={(e) => putList("marks", marks, i, { ...m, at: m.at.map((v, k) => (k === j ? num(e.target.value) : v)) as [number, number] })} />
              ))}
              <input type="number" step={15} aria-label="mark facing" value={m.facing_deg} title="0 faces the default cameras"
                onChange={(e) => putList("marks", marks, i, { ...m, facing_deg: num(e.target.value) })} />
              <button type="button" aria-label="remove mark" onClick={() => putList("marks", marks, i, null)}>✕</button>
            </div>
          ))}
          <button type="button" disabled={marks.length >= 32}
            onClick={() => putList("marks", marks, marks.length, { name: `mark_${marks.length + 1}`, kind: "spawn", at: [0, 0], facing_deg: 0 })}>Add mark</button>

          <h4>Camera marks <small>(with none, there are "wide" and "front")</small></h4>
          {cams.map((c, i) => (
            <div className="row" key={i}>
              <input value={c.name} aria-label="camera name" pattern="[a-z][a-z0-9_]{0,31}" onChange={(e) => putList("cameras", cams, i, { ...c, name: e.target.value })} />
              {(["at", "look_at"] as const).map((k) => [0, 1, 2].map((j) => (
                <input key={`${k}${j}`} type="number" step={0.1} aria-label={`camera ${k} ${"xyz"[j]}`} value={c[k][j]}
                  onChange={(e) => putList("cameras", cams, i, { ...c, [k]: c[k].map((v, n) => (n === j ? num(e.target.value) : v)) })} />
              )))}
              <input type="number" min={12} max={120} aria-label="lens" value={c.lens_mm} onChange={(e) => putList("cameras", cams, i, { ...c, lens_mm: num(e.target.value, 28) })} />
              <button type="button" aria-label="remove camera" onClick={() => putList("cameras", cams, i, null)}>✕</button>
            </div>
          ))}
          <button type="button" disabled={cams.length >= 12}
            onClick={() => putList("cameras", cams, cams.length, { name: `cam_${cams.length + 1}`, at: [0, -size[1] / 2 + 0.3, 1.4], look_at: [0, 0, 0.9], lens_mm: 28 })}>Add camera mark</button>
        </>
      )}
    </fieldset>
  );
}
