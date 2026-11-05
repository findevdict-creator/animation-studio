// One prop or environment version: its facts, the actions its status allows (save, generate,
// submit, review, publish, duplicate, recommend, deprecate, discard), the build check and its
// files. Props and environments share the library flow; ``KIND`` holds what differs.
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, EnvironmentVersionDetail, PropVersionDetail, SetDetail, SetPlural, SetVersionDetail, setFileUrl } from "../api/client";
import { useCan, useSetAction } from "../api/hooks";
import { bytes, ErrorNote, JobProgress, StatusChip, when } from "./bits";
import { ChecksReport } from "./ChecksReport";
import { EnvironmentSpecForm } from "./EnvironmentSpecForm";
import { PropSpecForm } from "./PropSpecForm";

const newKey = () => crypto.randomUUID();

export const KIND = {
  props: { label: "prop", title: "Prop Library", path: "/props", blank: { item: { id: "PROPD-BALL", version: "0.1" } },
    check: "Build check" },
  environments: { label: "environment", title: "Environment Library", path: "/environments",
    blank: { template: { id: "ENVD-LIVING_ROOM", version: "0.1" } }, check: "Layout check in Blender" },
} as const;

function Facts({ plural, v }: { plural: SetPlural; v: SetVersionDetail }) {
  if (plural === "props") {
    const p = v as PropVersionDetail;
    return (
      <>
        <dt>Item</dt><dd>{p.item_ref}</dd>
        <dt>Placement</dt><dd>{p.rests_on === "top" ? "on a table or desk top" : "on the floor"}{p.solid ? ", solid" : ", not solid"}</dd>
        {p.top_z != null && (<><dt>Top</dt><dd>{p.top_z.toFixed(2)} m</dd></>)}
        {p.seat_z != null && (<><dt>Seat</dt><dd>{p.seat_z.toFixed(2)} m</dd></>)}
      </>
    );
  }
  const e = v as EnvironmentVersionDetail;
  const layout = e.layout as { size?: number[]; marks?: { name: string }[]; cameras?: { name: string }[] } | null;
  return (
    <>
      <dt>Template</dt><dd>{e.template_ref}</dd>
      <dt>Lighting</dt><dd>{e.lighting}</dd>
      {layout?.size && (<><dt>Size</dt><dd>{layout.size.map((x) => x.toFixed(1)).join(" × ")} m</dd></>)}
      {layout?.marks && (<><dt>Marks</dt><dd>{layout.marks.map((m) => m.name).join(", ")}</dd></>)}
      {layout?.cameras && (<><dt>Camera marks</dt><dd>{layout.cameras.map((m) => m.name).join(", ")}</dd></>)}
    </>
  );
}

export function SetVersionPanel({ plural, owner, version }: { plural: SetPlural; owner: SetDetail; version: SetVersionDetail }) {
  const id = owner.id;
  const base = `/${plural}/${id}`;
  const k = KIND[plural];
  const navigate = useNavigate();
  const can = { create: useCan("asset.create"), edit: useCan("asset.edit_draft"), submit: useCan("asset.submit"),
    review: useCan("asset.review"), publish: useCan("asset.publish"), identity: useCan("asset.edit_identity") };
  const [job, setJob] = useState<string>();
  const [notes, setNotes] = useState("");
  const [bump, setBump] = useState<"minor" | "major">("minor");
  const [spec, setSpec] = useState(version.spec);
  useEffect(() => setSpec(version.spec), [version.id, version.spec]);
  const dirty = JSON.stringify(spec) !== JSON.stringify(version.spec);

  const save = useSetAction(plural, () => api(`${base}/drafts/${version.id}`, { method: "PATCH", json: { spec } }));
  const generate = useSetAction(plural, async () => {
    const r = await api<{ job: { id: string } }>(`${base}/drafts/${version.id}/generate`, { method: "POST", json: {}, idempotencyKey: newKey() });
    setJob(r.job.id);
  });
  const submit = useSetAction(plural, () => api(`${base}/drafts/${version.id}/submit`, { method: "POST" }));
  const review = useSetAction(plural, (decision: "approve" | "reject") => api(`${base}/versions/${version.id}/review`, { method: "POST", json: { decision, notes } }));
  const publish = useSetAction(plural, async () => {
    const r = await api<{ job: { id: string } }>(`${base}/versions/${version.id}/publish`, { method: "POST", json: { bump }, idempotencyKey: newKey() });
    setJob(r.job.id);
  });
  const duplicate = useSetAction(plural, async () => {
    const d = await api<SetVersionDetail>(`${base}/drafts`, { method: "POST", json: { based_on_version_id: version.id } });
    navigate(`${k.path}/${id}?version=${d.id}`);
  });
  const recommend = useSetAction(plural, () => api(base, { method: "PATCH", json: { recommended_version_id: version.id } }));
  const deprecate = useSetAction(plural, () => api(`${base}/versions/${version.id}/deprecate`, { method: "POST" }));
  const discard = useSetAction(plural, async () => {
    await api(`${base}/drafts/${version.id}`, { method: "DELETE" });
    navigate(`${k.path}/${id}`);
  });
  const all = [save, generate, submit, review, publish, duplicate, recommend, deprecate, discard];
  const busy = all.some((m) => m.isPending);
  const error = all.find((m) => m.error)?.error;
  const blockers = version.ready_for_review ?? [];

  return (
    <section className="panel" aria-label={`${k.label} version`}>
      <header className="panel-head">
        <h2>{version.version_label ? `Version ${version.version_label}` : "Draft"}</h2>
        <StatusChip status={version.status} />
        {version.review_decision && <span className={`chip chip-${version.review_decision}`}>{version.review_decision}</span>}
        {version.check_ok != null && (
          <span className={`chip chip-${version.check_ok ? "succeeded" : "failed"}`}>build check {version.check_ok ? "passed" : "failed"}</span>
        )}
      </header>
      <dl className="facts">
        <Facts plural={plural} v={version} />
        <dt>Blender</dt><dd>{version.blender_version ?? "not built"}</dd>
        {version.published_at && (<><dt>Published</dt><dd>{when(version.published_at)}</dd></>)}
        {version.manifest_sha256 && (<><dt>Manifest</dt><dd><code>{version.manifest_sha256.slice(0, 16)}…</code></dd></>)}
        {version.review_notes && (<><dt>Review notes</dt><dd>{version.review_notes}</dd></>)}
      </dl>

      {job && <JobProgress id={job} onDone={() => generate.reset()} />}
      <ErrorNote error={error} />

      {version.status === "draft" && (
        <div className="actions">
          {can.edit && (plural === "props"
            ? <PropSpecForm spec={spec} onChange={setSpec} lockItem />
            : <EnvironmentSpecForm spec={spec} onChange={setSpec} lockTemplate />)}
          <div className="buttons">
            {can.edit && <button disabled={!dirty || busy} onClick={() => save.mutate(undefined)}>Save spec</button>}
            {can.create && <button disabled={dirty || busy} onClick={() => generate.mutate(undefined)}>Generate and check</button>}
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
          {can.identity && version.status === "published" && owner.recommended_version_id !== version.id && (
            <button disabled={busy} onClick={() => recommend.mutate(undefined)}>Make recommended</button>
          )}
          {can.publish && version.status === "published" && (
            <button className="danger" disabled={busy} onClick={() => deprecate.mutate(undefined)}>Deprecate</button>
          )}
        </div>
      )}

      <ChecksReport title={k.check} report={version.check_report} />
      {version.files.length > 0 && (
        <table className="files">
          <thead><tr><th>File</th><th>Role</th><th>Size</th><th>SHA-256</th></tr></thead>
          <tbody>
            {version.files.map((f) => (
              <tr key={f.id}>
                <td><a href="#" onClick={async (e) => { e.preventDefault(); window.open(await setFileUrl(plural, id, version.id, f.name), "_blank"); }}>{f.name}</a></td>
                <td>{f.role}</td><td>{bytes(f.size_bytes)}</td><td><code>{f.sha256.slice(0, 12)}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
