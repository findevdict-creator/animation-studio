// The compatibility registry of one outfit version: which published character versions it
// was fit-checked on, the verdicts, approval and revocation, and a new check on a character.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, FitJobOut, FitOut, OutfitDetail, OutfitVersionDetail } from "../api/client";
import { useCan, useCharacter, useFits, useGallery, useOutfitAction } from "../api/hooks";
import { ErrorNote, JobProgress, StatusChip, when } from "./bits";

function CharacterPicker({ onPick, disabled, ownerId }: { onPick: (versionId: string) => void; disabled?: boolean; ownerId?: string | null }) {
  const gallery = useGallery({ status: "published" });
  const [cid, setCid] = useState(ownerId ?? "");
  const character = useCharacter(cid);
  const published = (character.data?.versions ?? []).filter((v) => v.status === "published" || v.status === "deprecated");
  const [vid, setVid] = useState("");
  const choices = (gallery.data?.items ?? []).filter((c) => !ownerId || c.id === ownerId);
  return (
    <div className="buttons">
      <select aria-label="character" value={cid} disabled={!!ownerId} onChange={(e) => { setCid(e.target.value); setVid(""); }}>
        <option value="">pick a published character…</option>
        {choices.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.code})</option>)}
      </select>
      <select aria-label="character version" value={vid} disabled={!cid} onChange={(e) => setVid(e.target.value)}>
        <option value="">version…</option>
        {published.map((v) => <option key={v.id} value={v.id}>v{v.version_label} · {v.fit_profile}</option>)}
      </select>
      <button className="primary" disabled={disabled || !vid} onClick={() => onPick(vid)}>Fit-check on this character</button>
    </div>
  );
}

export function FitsPanel({ outfit, version, selected, onSelect }: {
  outfit: OutfitDetail; version: OutfitVersionDetail; selected?: string; onSelect: (fit: FitOut | undefined) => void;
}) {
  const oid = outfit.id;
  const base = `/outfits/${oid}/versions/${version.id}/fits`;
  const fits = useFits(oid, version.id);
  const can = { create: useCan("asset.create"), review: useCan("asset.review") };
  const [job, setJob] = useState<string>();
  const request = useOutfitAction(oid, async (cvid: string) => {
    const r = await api<FitJobOut>(base, { method: "POST", json: { character_version_id: cvid }, idempotencyKey: crypto.randomUUID() });
    setJob(r.job.id);
    return r;
  });
  const decide = useOutfitAction(oid, ({ cvid, decision }: { cvid: string; decision: "approve" | "revoke" }) =>
    api<FitOut>(`${base}/${cvid}/decision`, { method: "POST", json: { decision } }));
  const built = version.files.some((f) => f.name === "outfit.blend");
  return (
    <section className="panel" aria-label="compatibility">
      <header className="panel-head"><h2>Compatibility</h2></header>
      <p className="readonly">
        Each row is one fit check of this exact outfit file on one published character version: the character is never
        copied or changed. Pick a row to try the outfit on that character above.
      </p>
      {job && <JobProgress id={job} onDone={() => { request.reset(); fits.refetch(); }} />}
      <ErrorNote error={request.error ?? decide.error ?? fits.error} />
      {(fits.data ?? []).length > 0 && (
        <table className="files fits">
          <thead><tr><th>Character</th><th>Fit profile</th><th>Verdict</th><th>Checked</th><th /></tr></thead>
          <tbody>
            {fits.data!.map((f) => (
              <tr key={f.id} className={f.character_version_id === selected ? "selected" : ""}>
                <td>
                  <Link to={`/characters/${f.character.id}?version=${f.character_version_id}`}>{f.character.name}</Link>{" "}
                  <small>{f.character.code} v{f.character.version}</small>
                </td>
                <td>{f.fit_profile}{f.fit_profile_declared === false && <span className="chip chip-failed">not declared</span>}</td>
                <td>
                  <StatusChip status={f.status} />
                  {!f.current && <span className="chip chip-failed">stale</span>}
                  {f.failed.length > 0 && <small> {f.failed.join(", ")}</small>}
                </td>
                <td>{when(f.checked_at)}</td>
                <td className="buttons">
                  {f.files.length > 0 && (
                    <button onClick={() => onSelect(f.character_version_id === selected ? undefined : f)}>
                      {f.character_version_id === selected ? "Back to reference" : "Try on"}
                    </button>
                  )}
                  {can.review && f.status === "verified" && (
                    <button className="primary" disabled={decide.isPending} onClick={() => decide.mutate({ cvid: f.character_version_id, decision: "approve" })}>Approve fit</button>
                  )}
                  {can.review && (f.status === "verified" || f.status === "approved") && (
                    <button className="danger" disabled={decide.isPending} onClick={() => decide.mutate({ cvid: f.character_version_id, decision: "revoke" })}>Revoke</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {can.create && built && version.status !== "deprecated" && (
        <CharacterPicker disabled={request.isPending} ownerId={outfit.scope === "character" ? outfit.owner_character_id : undefined}
          onPick={(cvid) => request.mutate(cvid)} />
      )}
      {!built && <p className="readonly">Generate the outfit before checking it on a character.</p>}
    </section>
  );
}
