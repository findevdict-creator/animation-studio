// Structured controls for an OutfitSpecification: one garment per slot from the catalog,
// its parameters within their declared ranges, fabric and colours from the style's clothing
// conventions. The form only offers values the validator (and the AI) accept.
import type { Conventions, Garment } from "../api/client";
import { useClothing, useGarments } from "../api/hooks";
import { Field } from "./bits";

type Spec = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
type Piece = { slot: string; garment: { id: string; version: string }; params?: Record<string, number>; material?: Record<string, string | undefined> };

export const SLOTS = ["upper", "lower", "footwear", "headwear", "gloves", "accessory"] as const;

function pieceFor(spec: Spec, slot: string): Piece | undefined {
  return (spec.pieces as Piece[] | undefined)?.find((p) => p.slot === slot);
}

function setPiece(spec: Spec, slot: string, piece: Piece | undefined): Spec {
  const rest = ((spec.pieces as Piece[] | undefined) ?? []).filter((p) => p.slot !== slot);
  const pieces = piece ? [...rest, piece] : rest;
  pieces.sort((a, b) => SLOTS.indexOf(a.slot as (typeof SLOTS)[number]) - SLOTS.indexOf(b.slot as (typeof SLOTS)[number]));
  return { ...spec, pieces };
}

function ColorSelect({ label, value, colors, onChange, allowNone }: {
  label: string; value?: string; colors: Record<string, string>; onChange: (v: string | undefined) => void; allowNone?: boolean;
}) {
  return (
    <Field label={label}>
      <span className="color-pick">
        <span className="swatch" style={{ background: value ? colors[value] ?? value : "transparent" }} />
        <select value={value ?? ""} onChange={(e) => onChange(e.target.value || undefined)}>
          <option value="">{allowNone ? "none" : "garment default"}</option>
          {Object.keys(colors).map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
        </select>
      </span>
    </Field>
  );
}

function PieceEditor({ garment, piece, conv, onChange }: {
  garment: Garment; piece: Piece; conv: Conventions; onChange: (p: Piece) => void;
}) {
  const material = piece.material ?? {};
  const setMat = (k: string, v: string | undefined) => onChange({ ...piece, material: { ...material, [k]: v } });
  return (
    <div className="piece-editor">
      {Object.entries(garment.params ?? {}).map(([k, d]) => {
        const v = piece.params?.[k] ?? d.default;
        return (
          <Field key={k} label={`${k.replace(/_/g, " ")} ${v.toFixed(2)}`}>
            <input type="range" min={d.min} max={d.max} step={(d.max - d.min) / 50} value={v} title={d.description}
              onChange={(e) => onChange({ ...piece, params: { ...piece.params, [k]: Number(e.target.value) } })} />
          </Field>
        );
      })}
      <Field label="Fabric">
        <select value={material.fabric ?? ""} onChange={(e) => setMat("fabric", e.target.value || undefined)}>
          <option value="">{garment.default_fabric} (default)</option>
          {garment.fabrics.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </Field>
      <ColorSelect label="Colour" value={material.color} colors={conv.colors} onChange={(v) => setMat("color", v)} />
      {garment.trim && <ColorSelect label="Trim colour" value={material.trim_color} colors={conv.colors} allowNone
        onChange={(v) => setMat("trim_color", v)} />}
    </div>
  );
}

export function OutfitSpecForm({ spec, onChange, lockScope }: { spec: Spec; onChange: (s: Spec) => void; lockScope?: boolean }) {
  const garments = useGarments();
  const clothing = useClothing(spec.style_profile ? `${spec.style_profile.id}@${spec.style_profile.version}` : undefined);
  const conv = clothing.data;
  if (!garments.data || !conv) return <div>loading garment catalog…</div>;
  const bySlot = (slot: string) => garments.data.filter((g) => g.slot === slot);
  const profiles: string[] = spec.fit_profiles ?? [];
  const scope = spec.scope ?? "body_profile";
  return (
    <div className="outfit-form">
      <div className="spec-form">
        <Field label="Scope">
          <select value={scope} disabled={lockScope} onChange={(e) => onChange({ ...spec, scope: e.target.value,
            owner_character: e.target.value === "character" ? spec.owner_character ?? "" : undefined })}>
            <option value="body_profile">a body-fit profile</option>
            <option value="multi">several body-fit profiles</option>
            <option value="character">one character</option>
          </select>
        </Field>
        {scope === "character" && (
          <Field label="Owner (character code)">
            <input placeholder="CHAR-001" disabled={lockScope} value={spec.owner_character ?? ""}
              onChange={(e) => onChange({ ...spec, owner_character: e.target.value.trim().toUpperCase() })} />
          </Field>
        )}
        <Field label="Fit profiles (first is built on)">
          <select multiple value={profiles} size={Math.min(4, conv.fit_profiles.length)}
            onChange={(e) => onChange({ ...spec, fit_profiles: Array.from(e.target.selectedOptions, (o) => o.value) })}>
            {conv.fit_profiles.map((f) => <option key={f} value={f}>{f.replace(/_/g, " ")}</option>)}
          </select>
        </Field>
      </div>
      {SLOTS.map((slot) => {
        const options = bySlot(slot);
        if (!options.length) return null;
        const piece = pieceFor(spec, slot);
        const garment = piece && options.find((g) => g.id === piece.garment.id);
        return (
          <fieldset key={slot} className="slot">
            <legend>{slot}</legend>
            <select aria-label={`${slot} garment`} value={garment?.id ?? ""} onChange={(e) => {
              const g = options.find((x) => x.id === e.target.value);
              onChange(setPiece(spec, slot, g ? { slot, garment: { id: g.id, version: g.version } } : undefined));
            }}>
              <option value="">none</option>
              {options.map((g) => <option key={g.id} value={g.id}>{g.label}</option>)}
            </select>
            {garment && piece && <PieceEditor garment={garment} piece={piece} conv={conv} onChange={(p) => onChange(setPiece(spec, slot, p))} />}
          </fieldset>
        );
      })}
    </div>
  );
}
