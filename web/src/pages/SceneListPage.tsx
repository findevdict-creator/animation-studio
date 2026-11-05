import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useCan, useScenes } from "../api/hooks";
import { ErrorNote, StatusChip, when } from "../components/bits";

export function SceneListPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const uses = params.get("uses_version_id") ?? undefined;
  const scenes = useScenes({ q: params.get("q") ?? undefined, uses_version_id: uses });
  const canEdit = useCan("scene.edit");
  return (
    <main>
      <div className="page-head">
        <h1>Scenes</h1>
        {canEdit && <Link className="button primary" to="/scenes/new">New scene</Link>}
      </div>
      <form className="filters" role="search" onSubmit={(e) => { e.preventDefault(); setParams(q ? { q } : {}); }}>
        <input type="search" placeholder="Search name, code, description" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" />
        <button type="submit">Search</button>
        {uses && <span className="chip">pinning version {uses.slice(-6)} <Link to="/scenes">clear</Link></span>}
      </form>
      <ErrorNote error={scenes.error} />
      {scenes.data && scenes.data.items.length === 0 && <p className="empty">No scenes yet.</p>}
      <ul className="gallery">
        {scenes.data?.items.map((s) => (
          <li key={s.id}>
            <Link to={`/scenes/${s.id}`} className="card">
              <div className="thumb wide">{s.latest_version?.thumbnail_url ? <img src={s.latest_version.thumbnail_url} alt={s.name} loading="lazy" /> : <span>not assembled yet</span>}</div>
              <div className="card-body">
                <strong>{s.name}</strong>
                <small>{s.code} · v{s.latest_version?.number} · {s.latest_version?.instances} instance(s)</small>
                {s.latest_version && (
                  <div className="card-meta">
                    <StatusChip status={`build ${s.latest_version.build_status}`.replace(" ", "-")} />
                    {s.latest_version.verify_status !== "none" && <StatusChip status={`reopen-${s.latest_version.verify_status}`} />}
                  </div>
                )}
                <small>{when(s.updated_at)}</small>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
