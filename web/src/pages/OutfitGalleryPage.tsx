import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useCan, useOutfits } from "../api/hooks";
import { ErrorNote } from "../components/bits";
import { SLOTS } from "../components/OutfitSpecForm";

export function OutfitGalleryPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const filter = { q: params.get("q") ?? undefined, slot: params.get("slot") ?? undefined,
    scope: params.get("scope") ?? undefined, status: params.get("status") ?? undefined };
  const outfits = useOutfits(filter);
  const canCreate = useCan("asset.create");
  const update = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v); else next.delete(k);
    setParams(next);
  };
  return (
    <main>
      <div className="page-head">
        <h1>Outfit Library</h1>
        {canCreate && <Link className="button primary" to="/outfits/new">New outfit</Link>}
      </div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); update("q", q); }} role="search">
        <input type="search" placeholder="Search name, code, description" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" />
        <select value={filter.slot ?? ""} onChange={(e) => update("slot", e.target.value)} aria-label="slot">
          <option value="">any slot</option>
          {SLOTS.map((s) => <option key={s} value={s}>has {s}</option>)}
        </select>
        <select value={filter.scope ?? ""} onChange={(e) => update("scope", e.target.value)} aria-label="scope">
          <option value="">any scope</option>
          <option value="character">one character</option>
          <option value="body_profile">a body-fit profile</option>
          <option value="multi">several profiles</option>
        </select>
        <select value={filter.status ?? ""} onChange={(e) => update("status", e.target.value)} aria-label="status">
          <option value="">any status</option>
          <option value="published">has published</option>
          <option value="review">in review</option>
          <option value="draft">has drafts</option>
        </select>
        <button type="submit">Search</button>
      </form>
      <ErrorNote error={outfits.error} />
      {outfits.data && outfits.data.items.length === 0 && <p className="empty">No outfits match.</p>}
      <ul className="gallery">
        {outfits.data?.items.map((o) => (
          <li key={o.id}>
            <Link to={`/outfits/${o.id}`} className="card">
              <div className="thumb">{o.thumbnail_url ? <img src={o.thumbnail_url} alt={o.name} loading="lazy" /> : <span>no build yet</span>}</div>
              <div className="card-body">
                <strong>{o.name}</strong>
                <small>{o.code} · {o.scope.replace("_", " ")}</small>
                <div className="card-meta">
                  {o.latest_published ? <span className="chip chip-published">v{o.latest_published}</span> : <span className="chip chip-draft">unpublished</span>}
                  {Object.entries(o.counts ?? {}).filter(([s]) => s !== "published").map(([s, n]) => <span key={s} className={`chip chip-${s}`}>{n} {s}</span>)}
                </div>
                <div className="tags">{o.tags.map((t) => <span key={t}>#{t}</span>)}</div>
              </div>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
