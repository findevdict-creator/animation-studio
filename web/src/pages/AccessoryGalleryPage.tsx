import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAccessories, useCan } from "../api/hooks";
import { CATEGORIES } from "../components/AccessorySpecForm";
import { ErrorNote } from "../components/bits";

export function AccessoryGalleryPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const filter = { q: params.get("q") ?? undefined, category: params.get("category") ?? undefined,
    status: params.get("status") ?? undefined };
  const list = useAccessories(filter);
  const canCreate = useCan("asset.create");
  const update = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  return (
    <main>
      <div className="page-head">
        <h1>Accessory Library</h1>
        {canCreate && <Link className="button primary" to="/accessories/new">New accessory</Link>}
      </div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); update("q", q); }} role="search">
        <input type="search" placeholder="Search name, code, description" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" />
        <select value={filter.category ?? ""} onChange={(e) => update("category", e.target.value)} aria-label="category">
          <option value="">any category</option>
          {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={filter.status ?? ""} onChange={(e) => update("status", e.target.value)} aria-label="status">
          <option value="">any status</option>
          <option value="published">has published</option>
          <option value="review">in review</option>
          <option value="draft">has drafts</option>
        </select>
        <button type="submit">Search</button>
      </form>
      <ErrorNote error={list.error} />
      {list.data && list.data.items.length === 0 && <p className="empty">No accessories match.</p>}
      <ul className="gallery">
        {list.data?.items.map((a) => (
          <li key={a.id}>
            <Link to={`/accessories/${a.id}`} className="card">
              <div className="thumb">{a.thumbnail_url ? <img src={a.thumbnail_url} alt={a.name} loading="lazy" /> : <span>no build yet</span>}</div>
              <div className="card-body">
                <strong>{a.name}</strong>
                <small>{a.code} · {a.category}</small>
                <div className="card-meta">
                  {a.latest_published ? <span className="chip chip-published">v{a.latest_published}</span> : <span className="chip chip-draft">unpublished</span>}
                </div>
                <div className="tags">{a.tags.map((t) => <span key={t}>#{t}</span>)}</div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
