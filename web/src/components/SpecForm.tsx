// Structured controls for a CharacterSpecification, generated from the style profile's
// vocabularies, so the form offers exactly the values the validator (and the AI) accept.
import { useQuery } from "@tanstack/react-query";
import { api, S } from "../api/client";
import { Field } from "./bits";

type Spec = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Profile = S["StyleProfile"];

export const useStyleProfile = (ref = "family_cartoon@1.0") =>
  useQuery({ queryKey: ["style", ref], queryFn: () => api<Profile>(`/style-profiles/${ref}`), staleTime: Infinity });

function set(spec: Spec, path: string, value: unknown): Spec {
  const [head, ...rest] = path.split(".");
  if (!rest.length) return { ...spec, [head]: value };
  return { ...spec, [head]: set(spec[head] ?? {}, rest.join("."), value) };
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (v: string) => void }) {
  return (
    <Field label={label}>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {options.map((o) => <option key={o} value={o}>{o.replace(/_/g, " ")}</option>)}
      </select>
    </Field>
  );
}

export function SpecForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const profile = useStyleProfile(spec.style_profile ? `${spec.style_profile.id}@${spec.style_profile.version}` : undefined);
  const p = profile.data;
  if (!p) return <div>loading style profile…</div>;
  const age = spec.body?.age_appearance ?? "child";
  const range = p.age_appearances[age]?.height_m;
  const up = (path: string) => (v: unknown) => onChange(set(spec, path, v));
  return (
    <div className="spec-form">
      <Select label="Age" value={age} options={Object.keys(p.age_appearances)}
        onChange={(a) => onChange(set(set(spec, "body.age_appearance", a), "body.height_m", p.age_appearances[a]?.height_m.default ?? null))} />
      <Select label="Body type" value={spec.body?.body_type ?? "average"} options={Object.keys(p.body_types)} onChange={up("body.body_type")} />
      <Field label={`Height (m) ${range ? `${range.min}–${range.max}` : ""}`}>
        <input type="number" step={0.01} min={range?.min} max={range?.max} value={spec.body?.height_m ?? range?.default ?? ""}
          onChange={(e) => up("body.height_m")(e.target.value === "" ? null : Number(e.target.value))} />
      </Field>
      <Select label="Face shape" value={spec.face?.shape ?? "rounded"} options={[...p.face_shapes]} onChange={up("face.shape")} />
      <Select label="Eye shape" value={spec.face?.eye_shape ?? "large_rounded"} options={[...p.eye_shapes]} onChange={up("face.eye_shape")} />
      <Select label="Eye colour" value={spec.face?.eye_color ?? "brown"} options={Object.keys(p.eye_colors)} onChange={up("face.eye_color")} />
      <Select label="Nose" value={spec.face?.nose ?? "small"} options={[...p.noses]} onChange={up("face.nose")} />
      <Select label="Mouth" value={spec.face?.mouth ?? "friendly"} options={[...p.mouths]} onChange={up("face.mouth")} />
      <Select label="Hair" value={spec.hair?.style ?? "short"} options={[...p.hair_styles]} onChange={up("hair.style")} />
      <Select label="Hair colour" value={spec.hair?.color ?? "dark_brown"} options={Object.keys(p.hair_colors)} onChange={up("hair.color")} />
      <Select label="Skin" value={spec.materials?.skin?.preset ?? "warm_medium"} options={Object.keys(p.skin_presets)} onChange={up("materials.skin.preset")} />
    </div>
  );
}
