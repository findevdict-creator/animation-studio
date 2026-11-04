import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useCan, useGallery } from "../api/hooks";
import { ErrorNote } from "../components/bits";

export function GalleryPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const filter = { q: params.get("q") ?? undefined, tag: params.get("tag") ?? undefined, status: params.get("status") ?? undefined };
  const gallery = useGallery(filter);
  const canCreate = useCan("asset.create");
  const update = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  return (
    <main>
      <div className="page-head">
        <h1>Character Library</h1>
        {canCreate && <Link className="button primary" to="/characters/new">New character</Link>}
      </div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); update("q", q); }} role="search">
        <input type="search" placeholder="Search name, code, description" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" />
        <input placeholder="tag" defaultValue={filter.tag} onBlur={(e) => update("tag", e.target.value.trim())} aria-label="tag" />
        <select value={filter.status ?? ""} onChange={(e) => update("status", e.target.value)} aria-label="status">
          <option value="">any status</option>
          <option value="published">has published</option>
          <option value="review">in review</option>
          <option value="draft">has drafts</option>
          <option value="deprecated">has deprecated</option>
        </select>
        <button type="submit">Search</button>
      </form>
      <ErrorNote error={gallery.error} />
      {gallery.data && gallery.data.items.length === 0 && <p className="empty">No characters match.</p>}
      <ul className="gallery">
        {gallery.data?.items.map((c) => (
          <li key={c.id}>
            <Link to={`/characters/${c.id}`} className="card">
              <div className="thumb">{c.thumbnail_url ? <img src={c.thumbnail_url} alt={c.name} loading="lazy" /> : <span>no build yet</span>}</div>
              <div className="card-body">
                <strong>{c.name}</strong>
                <small>{c.code}</small>
                <div className="card-meta">
                  {c.latest_published ? <span className="chip chip-published">v{c.latest_published}</span> : <span className="chip chip-draft">unpublished</span>}
                  {Object.entries(c.counts ?? {}).filter(([s]) => s !== "published").map(([s, n]) => <span key={s} className={`chip chip-${s}`}>{n} {s}</span>)}
                </div>
                <div className="tags">{c.tags.map((t) => <span key={t}>#{t}</span>)}</div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
