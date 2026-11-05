// Structured controls for an AnimationSpecification: either a procedural generator with its
// parameters inside their declared ranges (and an expression for GEN-EXPR), or a BVH take
// uploaded here and the bone map it is retargeted with; then the body-fit profiles it is
// declared for. Validation happens on the server when the draft is saved and built.
import { useState } from "react";
import { upload, type GeneratorDef } from "../api/client";
import { useBoneMaps, useClothing, useGenerators } from "../api/hooks";
import { ErrorNote, Field } from "./bits";
import { EXPRESSIONS } from "./InstanceEditor";

type Spec = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

export const CATEGORIES = ["locomotion", "idle", "gesture", "talk", "expression"] as const;

export function generatorFor(gens: GeneratorDef[] | undefined, spec: Spec): GeneratorDef | undefined {
  return spec.source?.kind === "generator" ? gens?.find((g) => g.id === spec.source?.generator?.id) : undefined;
}

export function AnimationSpecForm({ spec, onChange, lockSource }: { spec: Spec; onChange: (s: Spec) => void; lockSource?: boolean }) {
  const gens = useGenerators();
  const maps = useBoneMaps();
  const clothing = useClothing();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const src = spec.source ?? {};
  const gen = generatorFor(gens.data, spec);
  const profiles: string[] = spec.fit_profiles ?? [];
  const allProfiles = clothing.data?.fit_profiles ?? [];
  const setSrc = (patch: Spec) => onChange({ ...spec, source: { ...src, ...patch } });
  if (!gens.data) return <p>loading the generators…</p>;

  const take = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(undefined);
    try {
      const up = await upload("bvh", file);
      onChange({ ...spec, category: spec.category ?? "locomotion", source: {
        kind: "bvh", skeleton: src.skeleton ?? "cmu", licence: src.licence ?? "", travels: src.travels ?? false,
        loops: src.loops ?? false, upload_key: up.storage_key, sha256: up.sha256, source_name: up.name } });
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <fieldset className="spec-form">
      <legend>Animation</legend>
      <Field label="Made from">
        <select value={src.kind ?? "generator"} disabled={lockSource} aria-label="source"
          onChange={(e) => e.target.value === "bvh"
            ? onChange({ ...spec, category: spec.category ?? "locomotion", source: { kind: "bvh", skeleton: "cmu", licence: "" } })
            : onChange({ ...spec, category: undefined, source: { kind: "generator", generator: { id: gens.data[0].id, version: gens.data[0].version } } })}>
          <option value="generator">a generator</option>
          <option value="bvh">a motion-capture take (BVH)</option>
        </select>
      </Field>

      {src.kind !== "bvh" && (
        <>
          <Field label="Generator">
            <select value={src.generator?.id ?? ""} disabled={lockSource} aria-label="generator"
              onChange={(e) => {
                const g = gens.data.find((x) => x.id === e.target.value);
                if (g) setSrc({ generator: { id: g.id, version: g.version }, params: {}, expression: undefined });
              }}>
              {gens.data.map((g) => <option key={g.id} value={g.id}>{g.label} ({g.category})</option>)}
            </select>
          </Field>
          {gen && (
            <p className="hint">
              {gen.frames} frames at 24 fps{gen.loops ? ", loops" : ""}{gen.travels ? ", travels forward" : ""}.
            </p>
          )}
          {gen?.needs_expression && (
            <Field label="Expression">
              <select value={src.expression ?? ""} aria-label="expression" onChange={(e) => setSrc({ expression: e.target.value })}>
                <option value="" disabled>choose…</option>
                {EXPRESSIONS.map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </Field>
          )}
          {Object.entries(gen?.params ?? {}).map(([k, d]) => {
            const v = src.params?.[k] ?? d.default;
            return (
              <Field key={k} label={`${k.replace(/_/g, " ")} ${Number(v).toFixed(2)}`}>
                <input type="range" min={d.min} max={d.max} step={(d.max - d.min) / 50} value={v} title={d.description}
                  aria-label={k} onChange={(e) => setSrc({ params: { ...src.params, [k]: Number(e.target.value) } })} />
              </Field>
            );
          })}
        </>
      )}

      {src.kind === "bvh" && (
        <>
          {!lockSource && (
            <Field label="BVH file">
              <input type="file" accept=".bvh" aria-label="BVH file" disabled={busy} onChange={(e) => take(e.target.files?.[0])} />
            </Field>
          )}
          {src.upload_key && <p className="hint">Take <code>{src.source_name || src.upload_key}</code> · SHA-256 <code>{String(src.sha256).slice(0, 12)}</code></p>}
          <ErrorNote error={error} />
          <Field label="Skeleton">
            <select value={src.skeleton ?? "cmu"} aria-label="skeleton" onChange={(e) => setSrc({ skeleton: e.target.value })}>
              {(maps.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
          </Field>
          <Field label="Category">
            <select value={spec.category ?? "locomotion"} aria-label="category" onChange={(e) => onChange({ ...spec, category: e.target.value })}>
              {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Source frames">
            <span className="row">
              <input type="number" min={0} placeholder="first" aria-label="first frame" value={src.frame_start ?? ""}
                onChange={(e) => setSrc({ frame_start: e.target.value === "" ? undefined : Number(e.target.value) })} />
              <input type="number" min={0} placeholder="last" aria-label="last frame" value={src.frame_end ?? ""}
                onChange={(e) => setSrc({ frame_end: e.target.value === "" ? undefined : Number(e.target.value) })} />
            </span>
          </Field>
          <label className="check"><input type="checkbox" checked={!!src.travels} onChange={(e) => setSrc({ travels: e.target.checked })} /> the take moves through space (a walk): keep its travel as root motion</label>
          <label className="check"><input type="checkbox" checked={!!src.loops} onChange={(e) => setSrc({ loops: e.target.checked })} /> the take loops</label>
          <Field label="Licence and provenance">
            <input required maxLength={200} value={src.licence ?? ""} aria-label="licence" placeholder="CMU mocap, free use"
              onChange={(e) => setSrc({ licence: e.target.value })} />
          </Field>
        </>
      )}

      <Field label="Body-fit profiles">
        <span className="row">
          {allProfiles.map((p) => (
            <label key={p} className="check">
              <input type="checkbox" checked={profiles.includes(p)}
                onChange={(e) => onChange({ ...spec, fit_profiles: e.target.checked ? [...profiles, p] : profiles.filter((x) => x !== p) })} />
              {p.replace(/_/g, " ")}
            </label>
          ))}
        </span>
      </Field>
      <p className="hint">{profiles.length ? `Checked on ${profiles[0].replace(/_/g, " ")} first, then the others.` : "No profile ticked: every body of the style may use it."}</p>
    </fieldset>
  );
}
