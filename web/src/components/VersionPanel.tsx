import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, CharacterDetail, fileUrl, VersionDetail, VersionJobOut } from "../api/client";
import { useCan, useCharacterAction } from "../api/hooks";
import { bytes, ErrorNote, JobProgress, StatusChip, when } from "./bits";
import { SpecForm } from "./SpecForm";

function newKey(): string {
  return crypto.randomUUID();
}

export function VersionPanel({ character, version }: { character: CharacterDetail; version: VersionDetail }) {
  const cid = character.id;
  const base = `/characters/${cid}`;
  const navigate = useNavigate();
  const can = { create: useCan("asset.create"), edit: useCan("asset.edit_draft"), submit: useCan("asset.submit"),
    review: useCan("asset.review"), publish: useCan("asset.publish"), identity: useCan("asset.edit_identity") };
  const qc = useQueryClient();
  const [job, setJob] = useState<string>();
  const [notes, setNotes] = useState("");
  const [bump, setBump] = useState<"minor" | "major">("minor");
  const [spec, setSpec] = useState(version.spec);
  useEffect(() => setSpec(version.spec), [version.id, version.spec]);
  const dirty = JSON.stringify(spec) !== JSON.stringify(version.spec);

  const save = useCharacterAction(cid, () => api<VersionDetail>(`${base}/drafts/${version.id}`, { method: "PATCH", json: { spec } }));
  const generate = useCharacterAction(cid, async () => {
    const r = await api<VersionJobOut>(`${base}/drafts/${version.id}/generate`, { method: "POST", json: {}, idempotencyKey: newKey() });
    setJob(r.job.id);
    return r;
  });
  const submit = useCharacterAction(cid, () => api<VersionDetail>(`${base}/drafts/${version.id}/submit`, { method: "POST" }));
  const review = useCharacterAction(cid, (decision: "approve" | "reject") =>
    api<VersionDetail>(`${base}/versions/${version.id}/review`, { method: "POST", json: { decision, notes } }));
  const publish = useCharacterAction(cid, async () => {
    const r = await api<VersionJobOut>(`${base}/versions/${version.id}/publish`, { method: "POST", json: { bump }, idempotencyKey: newKey() });
    setJob(r.job.id);
    return r;
  });
  const duplicate = useCharacterAction(cid, async () => {
    const d = await api<VersionDetail>(`${base}/drafts`, { method: "POST", json: { based_on_version_id: version.id } });
    navigate(`/characters/${cid}?version=${d.id}`);
    return d;
  });
  const recommend = useCharacterAction(cid, () => api(`${base}`, { method: "PATCH", json: { recommended_version_id: version.id } }));
  const deprecate = useCharacterAction(cid, () => api<VersionDetail>(`${base}/versions/${version.id}/deprecate`, { method: "POST" }));
  const discard = useCharacterAction(cid, async () => {
    await api(`${base}/drafts/${version.id}`, { method: "DELETE" });
    navigate(`/characters/${cid}`);
    return version;
  });
  const busy = [save, generate, submit, review, publish, duplicate, recommend, deprecate, discard].some((m) => m.isPending);
  const error = [save, generate, submit, review, publish, duplicate, recommend, deprecate, discard].find((m) => m.error)?.error;
  const done = () => {
    for (const key of [["character", cid], ["version", cid], ["characters"]]) qc.invalidateQueries({ queryKey: key });
  };
  const report = version.validation_report as { ok?: boolean; checks?: { name: string; ok: boolean }[] } | null;

  return (
    <section className="panel" aria-label="version">
      <header className="panel-head">
        <h2>{version.version_label ? `Version ${version.version_label}` : "Draft"}</h2>
        <StatusChip status={version.status} />
        {version.review_decision && <span className={`chip chip-${version.review_decision}`}>{version.review_decision}</span>}
      </header>
      <dl className="facts">
        <dt>Fit profile</dt><dd>{version.fit_profile}</dd>
        <dt>Height</dt><dd>{version.height_m?.toFixed(2)} m</dd>
        <dt>Rig</dt><dd>{version.rig_profile_ref}</dd>
        <dt>Template</dt><dd>{version.template_ref}</dd>
        <dt>Blender</dt><dd>{version.blender_version ?? "not built"}</dd>
        {version.published_at && (<><dt>Published</dt><dd>{when(version.published_at)}</dd></>)}
        {version.manifest_sha256 && (<><dt>Manifest</dt><dd><code>{version.manifest_sha256.slice(0, 16)}…</code></dd></>)}
        {version.review_notes && (<><dt>Review notes</dt><dd>{version.review_notes}</dd></>)}
      </dl>

      {job && <JobProgress id={job} onDone={done} />}
      <ErrorNote error={error} />

      {version.status === "draft" && (
        <div className="actions">
          {can.edit && <SpecForm spec={spec} onChange={setSpec} />}
          <div className="buttons">
            {can.edit && <button disabled={!dirty || busy} onClick={() => save.mutate(undefined)}>Save spec</button>}
            {can.create && <button disabled={dirty || busy} onClick={() => generate.mutate(undefined)}>Generate in Blender</button>}
            {can.submit && <button className="primary" disabled={dirty || busy || (version.ready_for_review ?? []).length > 0} onClick={() => submit.mutate(undefined)}>Submit for review</button>}
            {can.edit && <button className="danger" disabled={busy} onClick={() => confirm("Discard this draft?") && discard.mutate(undefined)}>Discard</button>}
          </div>
          {(version.ready_for_review ?? []).length > 0 && (
            <ul className="blockers">{(version.ready_for_review ?? []).map((p) => <li key={p}>{p}</li>)}</ul>
          )}
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
              <button className="primary" disabled={busy || version.review_decision !== "approve"} onClick={() => publish.mutate(undefined)}>
                Publish
              </button>
            </div>
          )}
        </div>
      )}

      {(version.status === "published" || version.status === "deprecated") && (
        <div className="actions buttons">
          <span className="readonly">Published versions are read-only.</span>
          {can.create && <button className="primary" disabled={busy} onClick={() => duplicate.mutate(undefined)}>Duplicate as draft</button>}
          {can.identity && version.status === "published" && character.recommended_version_id !== version.id && (
            <button disabled={busy} onClick={() => recommend.mutate(undefined)}>Make recommended</button>
          )}
          {can.publish && version.status === "published" && (
            <button className="danger" disabled={busy} onClick={() => deprecate.mutate(undefined)}>Deprecate</button>
          )}
        </div>
      )}

      {report?.checks && (
        <details className="report">
          <summary>Validation: {report.ok ? "passed" : "failed"} ({report.checks.filter((c) => c.ok).length}/{report.checks.length} checks)</summary>
          <ul>{report.checks.map((c) => <li key={c.name} className={c.ok ? "ok" : "bad"}>{c.ok ? "✓" : "✗"} {c.name}</li>)}</ul>
        </details>
      )}
      {version.files.length > 0 && (
        <table className="files">
          <thead><tr><th>File</th><th>Role</th><th>Size</th><th>SHA-256</th></tr></thead>
          <tbody>
            {version.files.map((f) => (
              <tr key={f.id}>
                <td><a href="#" onClick={async (e) => { e.preventDefault(); window.open(await fileUrl(cid, version.id, f.name), "_blank"); }}>{f.name}</a></td>
                <td>{f.role}</td><td>{bytes(f.size_bytes)}</td><td><code>{f.sha256.slice(0, 12)}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
