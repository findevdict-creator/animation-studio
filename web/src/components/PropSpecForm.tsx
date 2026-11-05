// Structured controls for a PropSpecification: one catalog item, its parameters within their
// declared ranges, and finish and colours from the style's conventions. The size it comes out
// at (and its seat or top height) is shown so a scene can be planned around it.
import type { PropDef } from "../api/client";
import { useClothing, usePropItems } from "../api/hooks";
import { Field } from "./bits";

type Spec = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export const PROP_CATEGORIES = ["furniture", "household", "toy", "nature", "vehicle", "food"] as const;

/** Width, depth, height in metres for these parameters (the contract's formula). */
export function propSize(item: PropDef, params: Record<string, number> = {}): [number, number, number] {
  const p = (k: string) => params[k] ?? item.params?.[k]?.default ?? 0.5;
  const has = (k: string) => k in (item.params ?? {});
  const s = has("size") ? 0.8 + 0.4 * p("size") : 1;
  const w = has("width") ? 0.75 + 0.5 * p("width") : 1;
  const h = has("height") ? 0.75 + 0.5 * p("height") : 1;
  const [bx, by, bz] = item.base_size;
  return [bx * s * w, by * s, bz * s * h];
}

export function PropSpecForm({ spec, onChange, lockItem }: { spec: Spec; onChange: (s: Spec) => void; lockItem?: boolean }) {
  const items = usePropItems();
  const clothing = useClothing();
  const item = items.data?.find((i) => i.id === spec.item?.id);
  const colors = clothing.data?.colors ?? {};
  const material = spec.material ?? {};
  const setMat = (k: string, v: string | undefined) => onChange({ ...spec, material: { ...material, [k]: v } });
  if (!items.data) return <p>loading the prop catalog…</p>;
  const size = item && propSize(item, spec.params);
  return (
    <fieldset className="spec-form">
      <legend>Prop</legend>
      <Field label="Item">
        <select value={spec.item?.id ?? ""} disabled={lockItem} aria-label="item"
          onChange={(e) => {
            const next = items.data.find((i) => i.id === e.target.value);
            if (next) onChange({ ...spec, item: { id: next.id, version: next.version }, params: {}, material: {} });
          }}>
          <option value="" disabled>choose…</option>
          {items.data.map((i) => <option key={i.id} value={i.id}>{i.label} ({i.category})</option>)}
        </select>
      </Field>
      {item && size && (
        <>
          <p className="hint">
            {size.map((v) => v.toFixed(2)).join(" × ")} m · {item.solid ? "solid (characters walk around it)" : "not solid"}
            {" · "}{item.rests_on === "top" ? "goes on a table or desk" : "stands on the floor"}
            {item.surfaces.length > 0 && ` · ${item.surfaces.map((s) => `${s.kind} at ${(s.height_share * size[2]).toFixed(2)} m`).join(", ")}`}
            {item.setting !== "any" && ` · ${item.setting} only`}
          </p>
          {Object.entries(item.params ?? {}).map(([k, d]) => {
            const v = spec.params?.[k] ?? d.default;
            return (
              <Field key={k} label={`${k.replace(/_/g, " ")} ${Number(v).toFixed(2)}`}>
                <input type="range" min={d.min} max={d.max} step={(d.max - d.min) / 50} value={v} title={d.description} aria-label={k}
                  onChange={(e) => onChange({ ...spec, params: { ...spec.params, [k]: Number(e.target.value) } })} />
              </Field>
            );
          })}
          <Field label="Finish">
            <select value={material.finish ?? ""} onChange={(e) => setMat("finish", e.target.value || undefined)}>
              <option value="">{item.default_finish} (default)</option>
              {item.finishes.map((f) => <option key={f} value={f}>{f}</option>)}
            </select>
          </Field>
          <Field label="Colour">
            <span className="color-pick">
              <span className="swatch" style={{ background: material.color ? colors[material.color] ?? material.color : colors[item.default_color] }} />
              <select value={material.color ?? ""} onChange={(e) => setMat("color", e.target.value || undefined)}>
                <option value="">{item.default_color.replace(/_/g, " ")} (default)</option>
                {Object.keys(colors).map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
              </select>
            </span>
          </Field>
          {item.accent && (
            <Field label={`${item.accent} colour`}>
              <select value={material.accent_color ?? ""} onChange={(e) => setMat("accent_color", e.target.value || undefined)}>
                <option value="">{(item.default_accent ?? "default").replace(/_/g, " ")} (default)</option>
                {Object.keys(colors).map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
              </select>
            </Field>
          )}
        </>
      )}
    </fieldset>
  );
}
