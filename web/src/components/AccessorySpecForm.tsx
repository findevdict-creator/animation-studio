// Structured controls for an AccessorySpecification: one catalog item, its default attachment
// point (from the item's own points), its parameters within their declared ranges, finish and
// colours from the style's conventions, and the body-fit profiles it is declared for.
import type { AccessoryDef } from "../api/client";
import { useAccessoryItems, useClothing } from "../api/hooks";
import { Field } from "./bits";

type Spec = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export const CATEGORIES = ["eyewear", "headwear", "wristwear", "bag", "toy"] as const;

export function itemFor(items: AccessoryDef[] | undefined, spec: Spec): AccessoryDef | undefined {
  return items?.find((i) => i.id === spec.item?.id);
}

export function AccessorySpecForm({ spec, onChange, lockItem }: { spec: Spec; onChange: (s: Spec) => void; lockItem?: boolean }) {
  const items = useAccessoryItems();
  const clothing = useClothing();
  const item = itemFor(items.data, spec);
  const colors = clothing.data?.colors ?? {};
  const material = spec.material ?? {};
  const setMat = (k: string, v: string | undefined) => onChange({ ...spec, material: { ...material, [k]: v } });
  const profiles: string[] = spec.fit_profiles ?? [];
  const allProfiles = clothing.data?.fit_profiles ?? [];
  if (!items.data) return <p>loading the accessory catalog…</p>;
  return (
    <fieldset className="spec-form">
      <legend>Accessory</legend>
      <Field label="Item">
        <select value={spec.item?.id ?? ""} disabled={lockItem} aria-label="item"
          onChange={(e) => {
            const next = items.data.find((i) => i.id === e.target.value);
            if (next) onChange({ ...spec, item: { id: next.id, version: next.version }, attach_point: undefined, params: {}, material: {} });
          }}>
          <option value="" disabled>choose…</option>
          {items.data.map((i) => <option key={i.id} value={i.id}>{i.label} ({i.category})</option>)}
        </select>
      </Field>
      {item && (
        <>
          <Field label="Default attachment point">
            <select value={spec.attach_point ?? item.attach_points[0]} aria-label="attachment point"
              onChange={(e) => onChange({ ...spec, attach_point: e.target.value })}>
              {item.attach_points.map((p) => <option key={p} value={p}>{p.replace(/_/g, " ")}</option>)}
            </select>
          </Field>
          {Object.entries(item.params ?? {}).map(([k, d]) => {
            const v = spec.params?.[k] ?? d.default;
            return (
              <Field key={k} label={`${k.replace(/_/g, " ")} ${Number(v).toFixed(2)}`}>
                <input type="range" min={d.min} max={d.max} step={(d.max - d.min) / 50} value={v} title={d.description}
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
          <Field label="Declared for body-fit profiles (none ticked: every profile)">
            <span className="checks">
              {allProfiles.map((p) => (
                <label key={p}>
                  <input type="checkbox" checked={profiles.includes(p)}
                    onChange={(e) => onChange({ ...spec, fit_profiles: e.target.checked ? [...profiles, p] : profiles.filter((x) => x !== p) })} />
                  {p.replace(/_/g, " ")}
                </label>
              ))}
            </span>
          </Field>
          {item.conflicts_with_slots.length > 0 && (
            <p className="hint">Not worn with an outfit that fills {item.conflicts_with_slots.join(", ")}.</p>
          )}
        </>
      )}
    </fieldset>
  );
}
