// The prop and environment libraries: gallery, detail (previews, versions, the version panel)
// and a new-draft form. One set of pages serves both; ``plural`` picks the collection.
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, EnvironmentOut, EnvironmentVersionDetail, PropOut, SetDetail, SetOut, SetPlural, setFileUrl } from "../api/client";
import { useCan, useEnvironmentTemplates, useSet, useSets, useSetVersion } from "../api/hooks";
import { ErrorNote, Field } from "../components/bits";
import { EnvironmentSpecForm } from "../components/EnvironmentSpecForm";
import { GlbViewer } from "../components/GlbViewer";
import { PROP_CATEGORIES, PropSpecForm } from "../components/PropSpecForm";
import { asLayout, SetPlan } from "../components/SetPlan";
import { KIND, SetVersionPanel } from "../components/SetVersionPanel";
import { VersionTimeline } from "../components/VersionTimeline";

const subtitle = (plural: SetPlural, o: SetOut) =>
  plural === "props" ? (o as PropOut).category : `${(o as EnvironmentOut).template_key.replace(/_/g, " ")} · ${(o as EnvironmentOut).kind}`;

export function SetGalleryPage({ plural }: { plural: SetPlural }) {
  const k = KIND[plural];
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const filter = { q: params.get("q") ?? undefined, category: params.get("category") ?? undefined, status: params.get("status") ?? undefined };
  const list = useSets(plural, filter);
  const templates = useEnvironmentTemplates();
  const canCreate = useCan("asset.create");
  const categories = plural === "props" ? [...PROP_CATEGORIES] : (templates.data ?? []).map((t) => t.key);
  const update = (key: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(key, v); else next.delete(key);
    setParams(next);
  };
  return (
    <main>
      <div className="page-head">
        <h1>{k.title}</h1>
        {canCreate && <Link className="button primary" to={`${k.path}/new`}>New {k.label}</Link>}
      </div>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); update("q", q); }} role="search">
        <input type="search" placeholder="Search name, code, description" value={q} onChange={(e) => setQ(e.target.value)} aria-label="search" />
        <select value={filter.category ?? ""} onChange={(e) => update("category", e.target.value)} aria-label={plural === "props" ? "category" : "template"}>
          <option value="">{plural === "props" ? "any category" : "any template"}</option>
          {categories.map((c) => <option key={c} value={c}>{c.replace(/_/g, " ")}</option>)}
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
      {list.data && list.data.items.length === 0 && <p className="empty">No {plural} match.</p>}
      <ul className="gallery">
        {list.data?.items.map((o) => (
          <li key={o.id}>
            <Link to={`${k.path}/${o.id}`} className="card">
              <div className="thumb">{o.thumbnail_url ? <img src={o.thumbnail_url} alt={o.name} loading="lazy" /> : <span>no build yet</span>}</div>
              <div className="card-body">
                <strong>{o.name}</strong>
                <small>{o.code} · {subtitle(plural, o)}</small>
                <div className="card-meta">
                  {o.latest_published ? <span className="chip chip-published">v{o.latest_published}</span> : <span className="chip chip-draft">unpublished</span>}
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

export function SetPage({ plural }: { plural: SetPlural }) {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const owner = useSet(plural, id);
  const o = owner.data;
  const defaultVersion = useMemo(() => {
    if (!o) return undefined;
    const published = o.versions.filter((v) => v.status === "published");
    return o.recommended_version_id ?? published[0]?.id ?? o.versions[0]?.id;
  }, [o]);
  const vid = params.get("version") ?? defaultVersion;
  const version = useSetVersion(plural, id, vid);
  const v = version.data;
  const [urls, setUrls] = useState<Record<string, string>>({});
  useEffect(() => {
    setUrls({});
    if (!v) return;
    for (const name of ["preview.glb", "wide.png", "plan.png"]) {
      if (v.files.some((f) => f.name === name)) setFileUrl(plural, id, v.id, name).then((u) => setUrls((x) => ({ ...x, [name]: u })));
    }
  }, [plural, id, v?.id, v?.files.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (owner.error) return <main><ErrorNote error={owner.error} /></main>;
  if (!o) return <main>loading…</main>;
  const layout = plural === "environments" && v ? asLayout((v as EnvironmentVersionDetail).layout) : undefined;
  return (
    <main className="character">
      <div className="page-head">
        <h1>{o.name} <small>{o.code}</small></h1>
        <div className="tags">
          <span className="chip">{subtitle(plural, o)}</span>
          {o.tags.map((t) => <span key={t}>#{t}</span>)}
        </div>
      </div>
      {o.description && <p className="lede">{o.description}</p>}
      <div className="character-grid">
        <div>
          {urls["wide.png"] && (
            <figure className="pose-sheet">
              <img src={urls["wide.png"]} alt={`${o.name} from the wide camera`} loading="lazy" />
              <figcaption>Wide camera mark, with the version's lighting</figcaption>
            </figure>
          )}
          {urls["preview.glb"] ? <GlbViewer key={urls["preview.glb"]} url={urls["preview.glb"]} label={`${o.code} 3D preview`} />
            : <div className="viewer viewer-empty">{v ? "This version has no 3D preview yet." : ""}</div>}
          {layout && (
            <figure className="pose-sheet">
              <SetPlan layout={layout} label={`${o.name} plan`} />
              <figcaption>Plan: dressing, marks (with facing) and camera marks; the open side is the camera cutaway</figcaption>
            </figure>
          )}
        </div>
        <aside>
          <h2>Versions</h2>
          <VersionTimeline versions={o.versions} selected={vid} recommended={o.recommended_version_id}
            onSelect={(x) => setParams({ version: x })} />
        </aside>
      </div>
      <ErrorNote error={version.error} />
      {v && <SetVersionPanel key={v.id} plural={plural} owner={o} version={v} />}
    </main>
  );
}

export function NewSetPage({ plural }: { plural: SetPlural }) {
  const k = KIND[plural];
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [spec, setSpec] = useState<Record<string, unknown>>(k.blank);
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const o = await api<SetDetail>(`/${plural}`, { method: "POST", json: {
        name, description, tags: tags.split(/[\s,]+/).filter(Boolean).map((t) => t.toLowerCase()), spec: { ...spec, name, description } } });
      navigate(`${k.path}/${o.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main>
      <h1>New {k.label}</h1>
      <form className="new-character" onSubmit={create}>
        <Field label="Name"><input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description"><textarea maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Tags"><input placeholder="home, school" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        {plural === "props" ? <PropSpecForm spec={spec} onChange={setSpec} /> : <EnvironmentSpecForm spec={spec} onChange={setSpec} />}
        <ErrorNote error={error} />
        <button className="primary" disabled={busy || !name}>Create draft</button>
      </form>
    </main>
  );
}
