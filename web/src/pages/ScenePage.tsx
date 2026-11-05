import { useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, SceneVersionDetail, SceneVersionJobOut } from "../api/client";
import { useCan, useScene, useSceneAction, useSceneVersion } from "../api/hooks";
import { bytes, ErrorNote, JobProgress, StatusChip, when } from "../components/bits";
import { ChecksReport } from "../components/ChecksReport";
import { PinChips } from "../components/InstanceEditor";

const newKey = () => crypto.randomUUID();

type InstanceReport = { key: string; accessory_check?: { ok: boolean; failed: string[]; checks: { name: string; ok: boolean }[] } };

function BuildPanel({ sid, v }: { sid: string; v: SceneVersionDetail }) {
  const canEdit = useCan("scene.edit");
  const [job, setJob] = useState<string>();
  const base = `/scenes/${sid}/versions/${v.id}`;
  const assemble = useSceneAction(sid, async () => {
    const r = await api<SceneVersionJobOut>(`${base}/assemble`, { method: "POST", json: {}, idempotencyKey: newKey() });
    setJob(r.job.id);
  });
  const reopen = useSceneAction(sid, async () => {
    const r = await api<SceneVersionJobOut>(`${base}/reopen`, { method: "POST", json: {}, idempotencyKey: newKey() });
    setJob(r.job.id);
  });
  const busy = assemble.isPending || reopen.isPending || v.build_status === "queued" || v.verify_status === "queued";
  const frames = v.build_files.find((f) => f.name === "frames.png");
  const reopened = v.verify_files.find((f) => f.name === "reopened_frames.png");
  const video = v.build_files.find((f) => f.name === "preview.mp4");
  const instances = ((v.build_report ?? {}) as { instances?: InstanceReport[] }).instances ?? [];
  return (
    <section className="panel" aria-label="scene build">
      <header className="panel-head">
        <h2>Version {v.number}</h2>
        <StatusChip status={`build-${v.build_status}`} />
        <StatusChip status={`reopen-${v.verify_status}`} />
        {v.overrides > 0 && <span className="chip chip-failed">{v.overrides} fit override(s)</span>}
      </header>
      <dl className="facts">
        <dt>Manifest digest</dt><dd><code>{v.manifest_digest.slice(0, 16)}…</code></dd>
        <dt>Created</dt><dd>{when(v.created_at)}</dd>
        {v.notes && (<><dt>Notes</dt><dd>{v.notes}</dd></>)}
        {v.scene_blend_sha256 && (<><dt>scene.blend</dt><dd><code>{v.scene_blend_sha256.slice(0, 16)}…</code> built {when(v.built_at)} with Blender {v.blender_version}</dd></>)}
        {v.verified_at && (<><dt>Reopened</dt><dd>{when(v.verified_at)}</dd></>)}
      </dl>
      <PinChips resolved={v.resolved} />
      {job && <JobProgress id={job} onDone={() => { assemble.reset(); reopen.reset(); }} />}
      <ErrorNote error={assemble.error ?? reopen.error} />
      {canEdit && (
        <div className="buttons">
          <button className="primary" disabled={busy || !["none", "failed"].includes(v.build_status)} onClick={() => assemble.mutate(undefined)}>
            Assemble scene.blend
          </button>
          <button disabled={busy || v.build_status !== "built"} onClick={() => reopen.mutate(undefined)}>Reopen and check</button>
          <Link className="button" to={`/scenes/${sid}/edit?from=${v.id}`}>Edit as new version</Link>
        </div>
      )}
      {v.build_status === "built" && <p className="readonly">A built scene never changes; edits make a new version.</p>}
      {video?.url && (
        <figure className="scene-video">
          <video src={video.url} controls loop muted playsInline aria-label="preview video" />
          <figcaption>Preview video (low-sample Eevee, every few frames)</figcaption>
        </figure>
      )}
      <div className="frames">
        {frames?.url && <figure><img src={frames.url} alt="assembled frames" /><figcaption>Assembled: preview frames</figcaption></figure>}
        {reopened?.url && <figure><img src={reopened.url} alt="reopened frames" /><figcaption>Reopened in a fresh workspace</figcaption></figure>}
      </div>
      {instances.map((i) => i.accessory_check && <ChecksReport key={i.key} title={`Accessory check on ${i.key}`} report={i.accessory_check} />)}
      <ChecksReport title="Reopen check" report={v.verify_report} />
      {[...v.build_files, ...v.verify_files].length > 0 && (
        <table className="files">
          <thead><tr><th>File</th><th>Role</th><th>Size</th><th>SHA-256</th></tr></thead>
          <tbody>
            {[...v.build_files, ...v.verify_files].map((f) => (
              <tr key={f.name}><td>{f.url ? <a href={f.url} target="_blank" rel="noreferrer">{f.name}</a> : f.name}</td>
                <td>{f.role}</td><td>{bytes(f.size_bytes)}</td><td><code>{f.sha256.slice(0, 12)}</code></td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function ScenePage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const scene = useScene(id);
  const s = scene.data;
  const versions = useMemo(() => s?.versions ?? [], [s]);
  const latest = versions[versions.length - 1]?.id;
  const vid = params.get("version") ?? latest;
  const version = useSceneVersion(id, vid);
  if (scene.error) return <main><ErrorNote error={scene.error} /></main>;
  if (!s) return <main>loading…</main>;
  return (
    <main className="character">
      <div className="page-head">
        <h1>{s.name} <small>{s.code}</small></h1>
        <div className="tags">{s.tags.map((t) => <span key={t}>#{t}</span>)}</div>
      </div>
      {s.description && <p className="lede">{s.description}</p>}
      <div className="character-grid">
        <div>
          <ErrorNote error={version.error} />
          {version.data && <BuildPanel sid={id} v={version.data} />}
        </div>
        <aside>
          <h2>Versions</h2>
          <ol className="timeline" aria-label="scene versions">
            {[...versions].reverse().map((v) => (
              <li key={v.id} className={v.id === vid ? "selected" : ""}>
                <button onClick={() => setParams({ version: v.id })}>
                  <span className="tl-label">v{v.number}</span>
                  <StatusChip status={`build-${v.build_status}`} />
                  {v.verify_status !== "none" && <StatusChip status={`reopen-${v.verify_status}`} />}
                  <small>{v.based_on_version_id ? `from v${versions.find((x) => x.id === v.based_on_version_id)?.number} · ` : ""}{when(v.created_at)}</small>
                </button>
              </li>
            ))}
          </ol>
        </aside>
      </div>
    </main>
  );
}
