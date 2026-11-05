// The set of a scene manifest (Phase 7): the environment version it happens in, a lighting
// override, the camera mark, and the props placed in it (on the floor, or on the top of a
// dressing item or another prop). The plan shows the environment's layout with the scene's
// characters (at their marks) and props drawn over it. The server checks every placement
// against the same layout when the version is checked or saved.
import { EnvironmentDetail, EnvironmentOut, EnvironmentVersionDetail, PropOut } from "../api/client";
import { useLightingPresets, useSet, useSets, useSetVersion } from "../api/hooks";
import { Field } from "./bits";
import type { Instance, Pin } from "./InstanceEditor";
import { asLayout, Layout, PlanDot, SetPlan } from "./SetPlan";

export type PropUse = { key: string; prop: Pin; at: [number, number]; rotation_deg?: number; on?: string | null };
export type SetFields = { environment?: Pin | null; props?: PropUse[]; lighting?: string | null; camera: { preset: string; mark?: string | null } };

const num = (v: string, d = 0) => (v !== "" && Number.isFinite(Number(v)) ? Number(v) : d);

/** Where an instance starts and which way it faces: the contract's ``_start``, for the plan. */
export function startOf(inst: Instance, layout: Layout | undefined): { at: [number, number]; facing: number } {
  const loc = inst.transform?.location ?? [0, 0, 0];
  const rot = inst.transform?.rotation_deg?.[2] ?? 0;
  const mk = inst.mark && layout?.marks.find((m) => m.name === inst.mark);
  if (!mk) return { at: [loc[0], loc[1]], facing: rot };
  let [x, y] = mk.at;
  let facing = mk.facing_deg;
  const seat = mk.kind === "seat" ? layout?.seats.find((s) => s[0] === mk.item) : undefined;
  if (seat) {
    const [, cx, cy, , d, srot] = seat;
    const a = ((srot - 90) * Math.PI) / 180;
    x = cx + Math.cos(a) * (d / 2 + 0.22 + 0.05);
    y = cy + Math.sin(a) * (d / 2 + 0.22 + 0.05);
    facing = srot;
  }
  return { at: [x + loc[0], y + loc[1]], facing: facing + rot };
}

/** The environment version a pin names, and its layout. */
export function useEnvironmentLayout(pin?: Pin | null): Layout | undefined {
  const found = useSets<EnvironmentOut>("environments", { q: pin?.code });
  const id = found.data?.items.find((e) => e.code === pin?.code)?.id ?? "";
  const env = useSet<EnvironmentDetail>("environments", pin?.code ? id : "");
  const vid = env.data?.versions.find((v) => v.version_label === pin?.version)?.id;
  const version = useSetVersion<EnvironmentVersionDetail>("environments", id, vid);
  return pin?.code ? asLayout(version.data?.layout) : undefined;
}

function PublishedPicker({ plural, value, onChange, label, allowNone }: {
  plural: "props" | "environments"; value?: Pin | null; onChange: (p: Pin | null) => void; label: string; allowNone?: string;
}) {
  const list = useSets<PropOut | EnvironmentOut>(plural, { status: "published" });
  const items = (list.data?.items ?? []).filter((o) => o.latest_published);
  const v = value?.code ? `${value.code}@${value.version}` : "";
  return (
    <select value={v} aria-label={label}
      onChange={(e) => { if (!e.target.value) return onChange(null); const [code, version] = e.target.value.split("@"); onChange({ code, version }); }}>
      {allowNone ? <option value="">{allowNone}</option> : <option value="" disabled>choose a published {label}…</option>}
      {v && !items.some((o) => `${o.code}@${o.latest_published}` === v) && <option value={v}>{v}</option>}
      {items.map((o) => <option key={o.id} value={`${o.code}@${o.latest_published}`}>{o.name} ({o.code} v{o.latest_published})</option>)}
    </select>
  );
}

export function SceneSetEditor({ m, instances, onChange }: { m: SetFields; instances: Instance[]; onChange: (patch: Partial<SetFields>) => void }) {
  const layout = useEnvironmentLayout(m.environment);
  const lighting = useLightingPresets();
  const props = m.props ?? [];
  const put = (i: number, v: PropUse | null) => {
    const next = [...props];
    if (v === null) next.splice(i, 1); else next[i] = v;
    onChange({ props: next });
  };
  const tops = [...(layout?.tops ?? []).map((t) => t[0]), ...props.map((p) => p.key)];
  const dots: PlanDot[] = [
    ...props.map((p) => ({ key: p.key, at: p.at, kind: "prop" as const })),
    ...instances.map((i) => { const s = startOf(i, layout); return { key: i.key, at: s.at, facing_deg: s.facing, kind: "character" as const }; }),
  ];
  return (
    <fieldset className="scene-set" aria-label="set">
      <legend>Set</legend>
      <div className="grid2">
        <div>
          <Field label="Environment">
            <PublishedPicker plural="environments" label="environment" value={m.environment} allowNone="no environment (an empty stage)"
              onChange={(p) => onChange(p ? { environment: p } : { environment: null, camera: { ...m.camera, mark: null } })} />
          </Field>
          <div className="row">
            <Field label="Lighting">
              <select value={m.lighting ?? ""} aria-label="lighting" onChange={(e) => onChange({ lighting: e.target.value || null })}>
                <option value="">{layout ? `the environment's (${layout.lighting})` : "studio default"}</option>
                {(lighting.data ?? []).map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
              </select>
            </Field>
            <Field label="Camera mark">
              <select value={m.camera.mark ?? ""} aria-label="camera mark" disabled={!layout}
                onChange={(e) => onChange({ camera: { ...m.camera, mark: e.target.value || null } })}>
                <option value="">framing preset ({m.camera.preset})</option>
                {(layout?.cameras ?? []).map((c) => <option key={c.name} value={c.name}>{c.name} ({c.lens_mm} mm)</option>)}
              </select>
            </Field>
          </div>
          <h4>Props</h4>
          {props.map((p, i) => (
            <div className="row" key={i}>
              <input value={p.key} aria-label="prop key" pattern="[a-z][a-z0-9_]{0,31}" onChange={(e) => put(i, { ...p, key: e.target.value })} />
              <PublishedPicker plural="props" label="prop" value={p.prop} onChange={(x) => x && put(i, { ...p, prop: x })} />
              {(["x", "y"] as const).map((a, j) => (
                <input key={a} type="number" step={0.05} aria-label={`prop ${a}`} value={p.at[j]}
                  onChange={(e) => put(i, { ...p, at: p.at.map((v, k) => (k === j ? num(e.target.value) : v)) as [number, number] })} />
              ))}
              <input type="number" step={15} aria-label="prop turn" value={p.rotation_deg ?? 0} onChange={(e) => put(i, { ...p, rotation_deg: num(e.target.value) })} />
              <select value={p.on ?? ""} aria-label="prop on" onChange={(e) => put(i, { ...p, on: e.target.value || null })}>
                <option value="">on the floor</option>
                {tops.filter((t) => t !== p.key).map((t) => <option key={t} value={t}>on {t}</option>)}
              </select>
              <button type="button" aria-label="remove prop" onClick={() => put(i, null)}>✕</button>
            </div>
          ))}
          <button type="button" disabled={props.length >= 40}
            onClick={() => put(props.length, { key: `prop_${props.length + 1}`, prop: { code: "", version: "" }, at: [0, 0] })}>Add prop</button>
        </div>
        <div>
          {layout ? <SetPlan layout={layout} dots={dots} label="scene plan" highlight={m.camera.mark ? `camera:${m.camera.mark}` : undefined} />
            : <p className="hint">{m.environment ? "loading the environment's layout…" : "Pick an environment to see the plan, its marks and its camera marks."}</p>}
        </div>
      </div>
    </fieldset>
  );
}
