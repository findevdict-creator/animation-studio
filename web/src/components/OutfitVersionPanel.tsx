import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, OutfitDetail, OutfitVersionDetail, OutfitVersionJobOut, outfitFileUrl } from "../api/client";
import { useCan, useOutfitAction } from "../api/hooks";
import { bytes, ErrorNote, JobProgress, StatusChip, when } from "./bits";
import { FitReport } from "./FitReport";
import { OutfitSpecForm } from "./OutfitSpecForm";

const newKey = () => crypto.randomUUID();

export function OutfitVersionPanel({ outfit, version }: { outfit: OutfitDetail; version: OutfitVersionDetail }) {
  const oid = outfit.id;
  const base = `/outfits/${oid}`;
  const navigate = useNavigate();
  const can = { create: useCan("asset.create"), edit: useCan("asset.edit_draft"), submit: useCan("asset.submit"),
    review: useCan("asset.review"), publish: useCan("asset.publish"), identity: useCan("asset.edit_identity") };
  const [job, setJob] = useState<string>();
  const [notes, setNotes] = useState("");
  const [bump, setBump] = useState<"minor" | "major">("minor");
  const [spec, setSpec] = useState(version.spec);
  useEffect(() => setSpec(version.spec), [version.id, version.spec]);
  const dirty = JSON.stringify(spec) !== JSON.stringify(version.spec);

  const save = useOutfitAction(oid, () => api(`${base}/drafts/${version.id}`, { method: "PATCH", json: { spec } }));
  const generate = useOutfitAction(oid, async () => {
    const r = await api<OutfitVersionJobOut>(`${base}/drafts/${version.id}/generate`, { method: "POST", json: {}, idempotencyKey: newKey() });
    setJob(r.job.id);
  });
  const submit = useOutfitAction(oid, () => api(`${base}/drafts/${version.id}/submit`, { method: "POST" }));
  const review = useOutfitAction(oid, (decision: "approve" | "reject") =>
    api(`${base}/versions/${version.id}/review`, { method: "POST", json: { decision, notes } }));
  const publish = useOutfitAction(oid, async () => {
    const r = await api<OutfitVersionJobOut>(`${base}/versions/${version.id}/publish`, { method: "POST", json: { bump }, idempotencyKey: newKey() });
    setJob(r.job.id);
  });
  const duplicate = useOutfitAction(oid, async () => {
    const d = await api<OutfitVersionDetail>(`${base}/drafts`, { method: "POST", json: { based_on_version_id: version.id } });
    navigate(`/outfits/${oid}?version=${d.id}`);
  });
  const recommend = useOutfitAction(oid, () => api(base, { method: "PATCH", json: { recommended_version_id: version.id } }));
  const deprecate = useOutfitAction(oid, () => api(`${base}/versions/${version.id}/deprecate`, { method: "POST" }));
  const discard = useOutfitAction(oid, async () => {
    await api(`${base}/drafts/${version.id}`, { method: "DELETE" });
    navigate(`/outfits/${oid}`);
  });
  const all = [save, generate, submit, review, publish, duplicate, recommend, deprecate, discard];
  const busy = all.some((m) => m.isPending);
  const error = all.find((m) => m.error)?.error;
  const blockers = version.ready_for_review ?? [];

  return (
    <section className="panel" aria-label="outfit version">
      <header className="panel-head">
        <h2>{version.version_label ? `Version ${version.version_label}` : "Draft"}</h2>
        <StatusChip status={version.status} />
        {version.review_decision && <span className={`chip chip-${version.review_decision}`}>{version.review_decision}</span>}
        {version.fit_ok !== null && version.fit_ok !== undefined && (
          <span className={`chip chip-${version.fit_ok ? "succeeded" : "failed"}`}>fit {version.fit_ok ? "passed" : "failed"}</span>
        )}
      </header>
      <dl className="facts">
        <dt>Scope</dt><dd>{outfit.scope.replace("_", " ")}</dd>
        <dt>Fit profiles</dt><dd>{version.fit_profiles.join(", ")}</dd>
        <dt>Slots</dt><dd>{version.slots.join(", ")}</dd>
        <dt>Rig</dt><dd>{version.rig_profile_ref}</dd>
        <dt>Catalog</dt><dd>{version.catalog_ref}</dd>
        <dt>Blender</dt><dd>{version.blender_version ?? "not built"}</dd>
        {version.published_at && (<><dt>Published</dt><dd>{when(version.published_at)}</dd></>)}
        {version.manifest_sha256 && (<><dt>Manifest</dt><dd><code>{version.manifest_sha256.slice(0, 16)}…</code></dd></>)}
        {version.review_notes && (<><dt>Review notes</dt><dd>{version.review_notes}</dd></>)}
      </dl>

      {job && <JobProgress id={job} onDone={() => generate.reset()} />}
      <ErrorNote error={error} />

      {version.status === "draft" && (
        <div className="actions">
          {can.edit && <OutfitSpecForm spec={spec} onChange={setSpec} lockScope />}
          <div className="buttons">
            {can.edit && <button disabled={!dirty || busy} onClick={() => save.mutate(undefined)}>Save spec</button>}
            {can.create && <button disabled={dirty || busy} onClick={() => generate.mutate(undefined)}>Generate and fit-check</button>}
            {can.submit && <button className="primary" disabled={dirty || busy || blockers.length > 0} onClick={() => submit.mutate(undefined)}>Submit for review</button>}
            {can.edit && <button className="danger" disabled={busy} onClick={() => confirm("Discard this draft?") && discard.mutate(undefined)}>Discard</button>}
          </div>
          {blockers.length > 0 && <ul className="blockers">{blockers.map((p) => <li key={p}>{p}</li>)}</ul>}
        </div>
      )}

      {version.status === "review" && (
        <div className="actions">
          {can.review && (
            <div className="review">
              <textarea placeholder="Review notes" value={notes} onChange={(e) => setNotes(e.target.value)} />
              <div className="buttons">
                <button className="primary" disabled={busy} onClick={() => review.mutate("approve")}>Approve</button>
                <button disabled={busy} onClick={() => review.mutate("reject")}>Request changes</button>
              </div>
            </div>
          )}
          {can.publish && (
            <div className="buttons">
              <select value={bump} onChange={(e) => setBump(e.target.value as "minor" | "major")} aria-label="version bump">
                <option value="minor">minor bump</option>
                <option value="major">major bump</option>
              </select>
              <button className="primary" disabled={busy || version.review_decision !== "approve"} onClick={() => publish.mutate(undefined)}>Publish</button>
            </div>
          )}
        </div>
      )}

      {(version.status === "published" || version.status === "deprecated") && (
        <div className="actions buttons">
          <span className="readonly">Published versions are read-only.</span>
          {can.create && <button className="primary" disabled={busy} onClick={() => duplicate.mutate(undefined)}>Duplicate as draft</button>}
          {can.identity && version.status === "published" && outfit.recommended_version_id !== version.id && (
            <button disabled={busy} onClick={() => recommend.mutate(undefined)}>Make recommended</button>
          )}
          {can.publish && version.status === "published" && (
            <button className="danger" disabled={busy} onClick={() => deprecate.mutate(undefined)}>Deprecate</button>
          )}
        </div>
      )}

      <FitReport report={version.fit_report} />
      {version.files.length > 0 && (
        <table className="files">
          <thead><tr><th>File</th><th>Role</th><th>Size</th><th>SHA-256</th></tr></thead>
          <tbody>
            {version.files.map((f) => (
              <tr key={f.id}>
                <td><a href="#" onClick={async (e) => { e.preventDefault(); window.open(await outfitFileUrl(oid, version.id, f.name), "_blank"); }}>{f.name}</a></td>
                <td>{f.role}</td><td>{bytes(f.size_bytes)}</td><td><code>{f.sha256.slice(0, 12)}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
