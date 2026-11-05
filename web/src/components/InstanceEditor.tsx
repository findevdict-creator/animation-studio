// One character instance of a scene manifest: the exact character, outfit and accessory
// versions it pins (pickers offer published versions, and outfits from the character's
// wardrobe), and everything scene-specific: placement, expressions, clips, dialogue, state.
// The server checks every pin again when the version is saved.
import { useAccessories, useAccessory, useAnimations, useAudioClips, useCharacter, useClips, useGallery, useWardrobe } from "../api/hooks";
import { Field, StatusChip } from "./bits";

export const EXPRESSIONS = ["neutral", "happy", "sad", "angry", "surprised", "fearful", "confused", "excited", "laughing", "crying"];

export type Pin = { code: string; version: string };
export type AccessoryUse = { accessory: Pin; attach_point?: string | null };
export type Instance = {
  key: string; character: Pin; outfit?: Pin | null; accessories?: AccessoryUse[];
  transform?: { location?: number[]; rotation_deg?: number[]; scale?: number };
  expressions?: { frame: number; expression: string; weight?: number }[];
  blink?: boolean;
  animations?: { clip: string; start_frame: number; repeat?: number; speed?: number; influence?: number; travel?: boolean }[];
  dialogue?: { start_frame: number; end_frame: number; text: string; audio?: string | null }[];
  state?: Record<string, string | number | boolean>;
};

const num = (v: string, d = 0) => (Number.isFinite(Number(v)) && v !== "" ? Number(v) : d);

/** "mood=cheerful, holding=ball" <-> {mood: "cheerful", holding: "ball"} */
export function parseState(text: string): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const part of text.split(/[,\n]/)) {
    const [k, ...rest] = part.split("=");
    const key = k?.trim();
    if (!key) continue;
    const raw = rest.join("=").trim();
    out[key] = raw === "true" ? true : raw === "false" ? false : raw !== "" && Number.isFinite(Number(raw)) ? Number(raw) : raw;
  }
  return out;
}

export const stateText = (s?: Record<string, unknown>) => Object.entries(s ?? {}).map(([k, v]) => `${k}=${v}`).join(", ");

function CharacterPicker({ pin, onChange }: { pin: Pin; onChange: (p: Pin, id?: string) => void }) {
  const chars = useGallery({ status: "published" });
  const items = (chars.data?.items ?? []).filter((c) => c.latest_published);
  return (
    <Field label="Character">
      <select value={pin.code ? `${pin.code}@${pin.version}` : ""} aria-label="character"
        onChange={(e) => { const [code, version] = e.target.value.split("@"); onChange({ code, version }); }}>
        <option value="" disabled>choose a published character…</option>
        {pin.code && !items.some((c) => c.code === pin.code && c.latest_published === pin.version) && (
          <option value={`${pin.code}@${pin.version}`}>{pin.code}@{pin.version}</option>
        )}
        {items.map((c) => <option key={c.id} value={`${c.code}@${c.latest_published}`}>{c.name} ({c.code} v{c.latest_published})</option>)}
      </select>
    </Field>
  );
}

function OutfitPicker({ pin, outfit, onChange }: { pin: Pin; outfit?: Pin | null; onChange: (p: Pin | null) => void }) {
  const chars = useGallery({ q: pin.code });
  const charId = chars.data?.items.find((c) => c.code === pin.code)?.id;
  const character = useCharacter(charId ?? "");
  const cvid = character.data?.versions.find((v) => v.version_label === pin.version)?.id;
  const wardrobe = useWardrobe(charId ?? "", cvid);
  const fits = (wardrobe.data ?? []).filter((f) => f.outfit.version && (f.status === "approved" || f.status === "verified"));
  const value = outfit ? `${outfit.code}@${outfit.version}` : "";
  return (
    <Field label="Outfit (from this character version's wardrobe)">
      <select value={value} aria-label="outfit"
        onChange={(e) => { if (!e.target.value) return onChange(null); const [code, version] = e.target.value.split("@"); onChange({ code, version }); }}>
        <option value="">no outfit</option>
        {outfit && !fits.some((f) => `${f.outfit.code}@${f.outfit.version}` === value) && <option value={value}>{value}</option>}
        {fits.map((f) => (
          <option key={f.id} value={`${f.outfit.code}@${f.outfit.version}`}>
            {f.outfit.name} ({f.outfit.code} v{f.outfit.version}){f.status === "verified" ? " · fit not approved yet" : ""}
          </option>
        ))}
      </select>
    </Field>
  );
}

function AccessoryRow({ use, onChange, onRemove }: { use: AccessoryUse; onChange: (u: AccessoryUse) => void; onRemove: () => void }) {
  const list = useAccessories({ status: "published" });
  const items = (list.data?.items ?? []).filter((a) => a.latest_published);
  const id = items.find((a) => a.code === use.accessory.code)?.id;
  const detail = useAccessory(id ?? "");
  const version = detail.data?.versions.find((v) => v.version_label === use.accessory.version);
  return (
    <div className="row">
      <select value={use.accessory.code ? `${use.accessory.code}@${use.accessory.version}` : ""} aria-label="accessory"
        onChange={(e) => { const [code, v] = e.target.value.split("@"); onChange({ accessory: { code, version: v }, attach_point: null }); }}>
        <option value="" disabled>choose…</option>
        {items.map((a) => <option key={a.id} value={`${a.code}@${a.latest_published}`}>{a.name} ({a.code} v{a.latest_published})</option>)}
      </select>
      <select value={use.attach_point ?? ""} aria-label="attachment point" onChange={(e) => onChange({ ...use, attach_point: e.target.value || null })}>
        <option value="">{version?.attach_point ? `default (${version.attach_point})` : "default point"}</option>
        {(version?.attach_points ?? []).map((p) => <option key={p} value={p}>{p.replace(/_/g, " ")}</option>)}
      </select>
      <button type="button" onClick={onRemove} aria-label="remove accessory">✕</button>
    </div>
  );
}

export function InstanceEditor({ inst, frameEnd, onChange, onRemove }: {
  inst: Instance; frameEnd: number; onChange: (i: Instance) => void; onRemove: () => void;
}) {
  const clips = useClips();
  const library = useAnimations({ status: "published" });
  const audio = useAudioClips({ status: "ready" });
  const published = (library.data?.items ?? []).filter((a) => a.latest_published);
  const isLibrary = (clip: string) => clip.startsWith("ANIM-");
  const t = inst.transform ?? {};
  const loc = t.location ?? [0, 0, 0];
  const rot = t.rotation_deg ?? [0, 0, 0];
  const set = (patch: Partial<Instance>) => onChange({ ...inst, ...patch });
  const setT = (patch: Partial<NonNullable<Instance["transform"]>>) => set({ transform: { ...t, ...patch } });
  const list = <K extends "expressions" | "animations" | "dialogue" | "accessories">(k: K) => (inst[k] ?? []) as NonNullable<Instance[K]>;
  const put = <K extends "expressions" | "animations" | "dialogue" | "accessories">(k: K, i: number, v: NonNullable<Instance[K]>[number] | null) => {
    const arr = [...list(k)] as unknown[];
    if (v === null) arr.splice(i, 1); else arr[i] = v;
    set({ [k]: arr } as Partial<Instance>);
  };
  return (
    <fieldset className="instance" aria-label={`instance ${inst.key}`}>
      <legend>
        <input value={inst.key} aria-label="instance key" pattern="[a-z][a-z0-9_]{0,31}" onChange={(e) => set({ key: e.target.value })} />
        <button type="button" className="danger" onClick={onRemove}>Remove</button>
      </legend>
      <div className="grid2">
        <CharacterPicker pin={inst.character} onChange={(p) => set({ character: p, outfit: null })} />
        {inst.character.code && <OutfitPicker pin={inst.character} outfit={inst.outfit} onChange={(p) => set({ outfit: p })} />}
      </div>

      <h4>Accessories</h4>
      {list("accessories").map((u, i) => <AccessoryRow key={i} use={u} onChange={(v) => put("accessories", i, v)} onRemove={() => put("accessories", i, null)} />)}
      <button type="button" onClick={() => put("accessories", list("accessories").length, { accessory: { code: "", version: "" } })}>Add accessory</button>

      <h4>Placement</h4>
      <div className="row">
        {["x", "y", "z"].map((a, i) => (
          <Field key={a} label={`${a} (m)`}>
            <input type="number" step={0.05} value={loc[i]} onChange={(e) => setT({ location: loc.map((v, j) => (j === i ? num(e.target.value) : v)) })} />
          </Field>
        ))}
        <Field label="turn (°)">
          <input type="number" step={5} value={rot[2]} onChange={(e) => setT({ rotation_deg: [rot[0], rot[1], num(e.target.value)] })} />
        </Field>
        <Field label="scale">
          <input type="number" step={0.05} min={0.25} max={4} value={t.scale ?? 1} onChange={(e) => setT({ scale: num(e.target.value, 1) })} />
        </Field>
      </div>

      <h4>Expressions <label className="inline"><input type="checkbox" checked={inst.blink ?? true} onChange={(e) => set({ blink: e.target.checked })} /> blink</label></h4>
      {list("expressions").map((x, i) => (
        <div className="row" key={i}>
          <input type="number" aria-label="frame" min={0} max={frameEnd} value={x.frame} onChange={(e) => put("expressions", i, { ...x, frame: num(e.target.value) })} />
          <select value={x.expression} aria-label="expression" onChange={(e) => put("expressions", i, { ...x, expression: e.target.value })}>
            {EXPRESSIONS.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
          <input type="range" min={0} max={1} step={0.05} aria-label="weight" value={x.weight ?? 1} onChange={(e) => put("expressions", i, { ...x, weight: num(e.target.value, 1) })} />
          <small>{(x.weight ?? 1).toFixed(2)}</small>
          <button type="button" onClick={() => put("expressions", i, null)} aria-label="remove expression">✕</button>
        </div>
      ))}
      <button type="button" onClick={() => put("expressions", list("expressions").length, { frame: 1, expression: "happy", weight: 1 })}>Add expression key</button>

      <h4>Animation clips</h4>
      {list("animations").map((a, i) => (
        <div className="row" key={i}>
          <select value={a.clip} aria-label="clip" onChange={(e) => put("animations", i, { ...a, clip: e.target.value })}>
            <optgroup label="Animation library">
              {published.map((x) => <option key={x.id} value={`${x.code}@${x.latest_published}`}>{x.name} ({x.code}@{x.latest_published}, {x.category})</option>)}
            </optgroup>
            <optgroup label="Stand-in clips">
              {(clips.data ?? []).map((c) => <option key={c.id} value={`${c.id}@${c.version}`}>{c.label}</option>)}
            </optgroup>
            {!clips.data?.some((c) => `${c.id}@${c.version}` === a.clip) && !published.some((x) => `${x.code}@${x.latest_published}` === a.clip)
              && <option value={a.clip}>{a.clip}</option>}
          </select>
          <Field label="start"><input type="number" min={0} value={a.start_frame} onChange={(e) => put("animations", i, { ...a, start_frame: num(e.target.value) })} /></Field>
          <Field label="repeat"><input type="number" min={1} max={100} value={a.repeat ?? 1} onChange={(e) => put("animations", i, { ...a, repeat: num(e.target.value, 1) })} /></Field>
          {isLibrary(a.clip) && (
            <label className="inline" title="a walk moves the character forward along its facing; untick to walk on the spot">
              <input type="checkbox" checked={a.travel ?? true} onChange={(e) => put("animations", i, { ...a, travel: e.target.checked })} /> travel
            </label>
          )}
          <button type="button" onClick={() => put("animations", i, null)} aria-label="remove clip">✕</button>
        </div>
      ))}
      <button type="button" onClick={() => put("animations", list("animations").length, { clip: "CLIP-IDLE@0.1", start_frame: 1, repeat: 1 })}>Add clip</button>

      <h4>Dialogue</h4>
      {list("dialogue").map((d, i) => (
        <div className="row" key={i}>
          <input type="number" aria-label="from frame" value={d.start_frame} onChange={(e) => put("dialogue", i, { ...d, start_frame: num(e.target.value) })} />
          <input type="number" aria-label="to frame" value={d.end_frame} onChange={(e) => put("dialogue", i, { ...d, end_frame: num(e.target.value) })} />
          <input className="grow" aria-label="line" maxLength={500} value={d.text} onChange={(e) => put("dialogue", i, { ...d, text: e.target.value })} />
          <select value={d.audio ?? ""} aria-label="recording" title="with a recording the mouth follows it (lip sync); without, it flaps"
            onChange={(e) => {
              const clip = audio.data?.items.find((c) => c.code === e.target.value);
              put("dialogue", i, { ...d, audio: e.target.value || null, ...(clip?.dialog_text && !d.text.trim() ? { text: clip.dialog_text.slice(0, 500) } : {}) });
            }}>
            <option value="">no recording</option>
            {(audio.data?.items ?? []).map((c) => <option key={c.id} value={c.code}>{c.code} · {c.duration_s?.toFixed(1)} s · {(c.dialog_text ?? c.name).slice(0, 40)}</option>)}
            {d.audio && !audio.data?.items.some((c) => c.code === d.audio) && <option value={d.audio}>{d.audio}</option>}
          </select>
          <button type="button" onClick={() => put("dialogue", i, null)} aria-label="remove line">✕</button>
        </div>
      ))}
      <button type="button" onClick={() => put("dialogue", list("dialogue").length, { start_frame: 1, end_frame: 24, text: "Hello!" })}>Add line</button>

      <Field label="State (key=value, comma separated)">
        <input defaultValue={stateText(inst.state)} onBlur={(e) => set({ state: parseState(e.target.value) })} />
      </Field>
    </fieldset>
  );
}

export function PinChips({ resolved }: { resolved: Record<string, unknown> }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const insts = ((resolved as any).instances ?? []) as any[];
  return (
    <table className="pins">
      <thead><tr><th>Instance</th><th>Character</th><th>Outfit</th><th>Fit</th><th>Accessories</th><th>Clips</th></tr></thead>
      <tbody>
        {insts.map((i) => (
          <tr key={i.key}>
            <td>{i.key}</td>
            <td>{i.character.name} <code>{i.character.code}@{i.character.version}</code></td>
            <td>{i.outfit ? <><span>{i.outfit.name}</span> <code>{i.outfit.code}@{i.outfit.version}</code></> : "none"}</td>
            <td>{i.outfit_compatibility ? <><StatusChip status={i.outfit_compatibility.status} />{i.outfit_compatibility.override && <span className="chip chip-failed">director override</span>}</> : ""}</td>
            <td>{i.accessories.map((a: any) => <div key={a.pin.code}><code>{a.pin.code}@{a.pin.version}</code> on {a.attach_point}</div>)}</td>
            <td>{i.animations.map((a: any) => a.key).join(", ")}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
