// Write a scene manifest: a new scene, or a new version of one (?from=<version id> starts from
// that version's manifest and keeps the deprecated pins it already had). Saving creates an
// immutable version; "Check" is a dry run against the registry.
import { useEffect, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, SceneDetail, SceneValidationOut, SceneVersionDetail } from "../api/client";
import { useCan, useScene, useSceneVersion } from "../api/hooks";
import { ErrorNote, Field } from "../components/bits";
import { Instance, InstanceEditor, Pin, PinChips } from "../components/InstanceEditor";
import { PropUse, SceneSetEditor, useEnvironmentLayout } from "../components/SceneSetEditor";

export type Manifest = {
  name: string; description?: string; fps: number; frame_start: number; frame_end: number;
  instances: Instance[]; camera: { preset: string; angle_deg: number; focus?: string | null; mark?: string | null };
  environment?: Pin | null; props?: PropUse[]; lighting?: string | null;
};

export const blankInstance = (n: number): Instance => ({
  key: `char_${n}`, character: { code: "", version: "" }, outfit: null, accessories: [],
  transform: { location: [0, 0, 0], rotation_deg: [0, 0, 0], scale: 1 }, expressions: [], blink: true,
  animations: [{ clip: "CLIP-IDLE@0.1", start_frame: 1, repeat: 1 }], dialogue: [], state: {},
});

export const BLANK: Manifest = { name: "", fps: 24, frame_start: 1, frame_end: 72, instances: [blankInstance(1)],
  camera: { preset: "wide", angle_deg: 20, focus: null } };

/** The manifest as sent: empty accessory and prop rows, an unset focus and set fields
 * without an environment are left out. */
export function cleanManifest(m: Manifest): Manifest {
  const env = m.environment?.code ? m.environment : null;
  return { ...m, environment: env, lighting: m.lighting || null,
    props: (m.props ?? []).filter((p) => p.prop.code),
    camera: { ...m.camera, focus: m.camera.focus || null, mark: env ? m.camera.mark || null : null },
    instances: m.instances.map((i) => ({ ...i, mark: env ? i.mark || null : null,
      accessories: (i.accessories ?? []).filter((a) => a.accessory.code) })) };
}

export function SceneEditorPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const from = params.get("from") ?? undefined;
  const navigate = useNavigate();
  const scene = useScene(id);
  const base = useSceneVersion(id, from);
  const canEdit = useCan("scene.edit");
  const canOverride = useCan("asset.publish");
  const [m, setM] = useState<Manifest>(BLANK);
  const [notes, setNotes] = useState("");
  const [override, setOverride] = useState(false);
  const [check, setCheck] = useState<SceneValidationOut>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const layout = useEnvironmentLayout(m.environment);
  useEffect(() => { if (base.data) setM(base.data.manifest as unknown as Manifest); }, [base.data?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!from && scene.data) setM((x) => ({ ...x, name: x.name || scene.data!.name })); }, [scene.data?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (patch: Partial<Manifest>) => { setCheck(undefined); setM({ ...m, ...patch }); };
  const setInst = (i: number, v: Instance | null) => {
    const next = [...m.instances];
    if (v === null) next.splice(i, 1); else next[i] = v;
    set({ instances: next });
  };
  const run = async (what: "check" | "save") => {
    setBusy(true);
    setError(undefined);
    try {
      const manifest = cleanManifest(m);
      if (what === "check") {
        setCheck(await api<SceneValidationOut>("/scenes/validate", { method: "POST", json: {
          manifest, scene_id: id ?? null, based_on_version_id: from ?? null, allow_verified_fits: override } }));
      } else if (id) {
        const v = await api<SceneVersionDetail>(`/scenes/${id}/versions`, { method: "POST", json: {
          manifest, based_on_version_id: from ?? null, notes, allow_verified_fits: override } });
        navigate(`/scenes/${id}?version=${v.id}`);
      } else {
        const s = await api<SceneDetail>("/scenes", { method: "POST", json: {
          name: m.name, description: m.description ?? "", manifest, notes, allow_verified_fits: override } });
        navigate(`/scenes/${s.id}`);
      }
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  if (!canEdit) return <main><p className="readonly">Building scenes needs the scene.edit permission.</p></main>;
  return (
    <main>
      <h1>{id ? `New version of ${scene.data?.name ?? "scene"}` : "New scene"}{from && base.data ? <small> from v{base.data.number}</small> : null}</h1>
      <form className="scene-editor" onSubmit={(e) => { e.preventDefault(); run("save"); }}>
        <div className="grid2">
          <Field label="Name"><input required maxLength={120} value={m.name} onChange={(e) => set({ name: e.target.value })} /></Field>
          <Field label="Description"><input maxLength={4000} value={m.description ?? ""} onChange={(e) => set({ description: e.target.value })} /></Field>
        </div>
        <div className="row">
          <Field label="fps">
            <select value={m.fps} onChange={(e) => set({ fps: Number(e.target.value) })}>{[24, 25, 30].map((f) => <option key={f}>{f}</option>)}</select>
          </Field>
          <Field label="first frame"><input type="number" min={0} value={m.frame_start} onChange={(e) => set({ frame_start: Number(e.target.value) })} /></Field>
          <Field label="last frame"><input type="number" min={1} value={m.frame_end} onChange={(e) => set({ frame_end: Number(e.target.value) })} /></Field>
          <Field label="camera">
            <select value={m.camera.preset} disabled={!!m.camera.mark} onChange={(e) => set({ camera: { ...m.camera, preset: e.target.value } })}>
              {["wide", "medium", "close"].map((p) => <option key={p}>{p}</option>)}
            </select>
          </Field>
          <Field label="angle (°)"><input type="number" min={-90} max={90} value={m.camera.angle_deg} onChange={(e) => set({ camera: { ...m.camera, angle_deg: Number(e.target.value) } })} /></Field>
          <Field label="focus">
            <select value={m.camera.focus ?? ""} onChange={(e) => set({ camera: { ...m.camera, focus: e.target.value || null } })}>
              <option value="">everyone</option>
              {m.instances.map((i) => <option key={i.key} value={i.key}>{i.key}</option>)}
            </select>
          </Field>
        </div>

        <SceneSetEditor m={m} instances={m.instances} onChange={(patch) => set(patch as Partial<Manifest>)} />

        {m.instances.map((inst, i) => (
          <InstanceEditor key={i} inst={inst} frameEnd={m.frame_end} marks={layout?.marks} onChange={(v) => setInst(i, v)} onRemove={() => setInst(i, null)} />
        ))}
        <button type="button" disabled={m.instances.length >= 8} onClick={() => set({ instances: [...m.instances, blankInstance(m.instances.length + 1)] })}>
          Add character instance
        </button>

        <Field label="Version notes"><input maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>
        {canOverride && (
          <label className="inline">
            <input type="checkbox" checked={override} onChange={(e) => setOverride(e.target.checked)} />
            Director override: accept outfit fits that are verified but not yet approved (recorded on the version and audited)
          </label>
        )}
        <ErrorNote error={error} />
        {check && (
          <div className={check.ok ? "note ok" : "error"} role="status">
            {check.ok ? "Every pin resolves." : "The manifest cannot be saved yet:"}
            {check.issues.length > 0 && <ul>{check.issues.map((x, i) => <li key={i} className={x.severity}>{x.path}: {x.message}</li>)}</ul>}
            {check.resolved && <PinChips resolved={check.resolved} />}
          </div>
        )}
        <div className="buttons">
          <button type="button" disabled={busy} onClick={() => run("check")}>Check</button>
          <button className="primary" disabled={busy || !m.name}>{id ? "Save new version" : "Create scene"}</button>
        </div>
      </form>
    </main>
  );
}
