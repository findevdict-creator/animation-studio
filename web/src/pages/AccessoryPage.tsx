import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { accessoryFileUrl } from "../api/client";
import { useAccessory, useAccessoryVersion } from "../api/hooks";
import { AccessoryVersionPanel } from "../components/AccessoryVersionPanel";
import { ErrorNote } from "../components/bits";
import { GlbViewer } from "../components/GlbViewer";
import { VersionTimeline } from "../components/VersionTimeline";

export function AccessoryPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const accessory = useAccessory(id);
  const a = accessory.data;
  const defaultVersion = useMemo(() => {
    if (!a) return undefined;
    const published = a.versions.filter((v) => v.status === "published");
    return a.recommended_version_id ?? published[0]?.id ?? a.versions[0]?.id;
  }, [a]);
  const vid = params.get("version") ?? defaultVersion;
  const version = useAccessoryVersion(id, vid);
  const v = version.data;
  const [glb, setGlb] = useState<string>();
  const [onRef, setOnRef] = useState<string>();
  useEffect(() => {
    setGlb(undefined);
    setOnRef(undefined);
    if (!v) return;
    if (v.files.some((f) => f.name === "preview.glb")) accessoryFileUrl(id, v.id, "preview.glb").then(setGlb);
    if (v.files.some((f) => f.name === "on_reference.png")) accessoryFileUrl(id, v.id, "on_reference.png").then(setOnRef);
  }, [id, v?.id, v?.files.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (accessory.error) return <main><ErrorNote error={accessory.error} /></main>;
  if (!a) return <main>loading…</main>;
  return (
    <main className="character">
      <div className="page-head">
        <h1>{a.name} <small>{a.code}</small></h1>
        <div className="tags">
          <span className="chip">{a.category}</span>
          {a.tags.map((t) => <span key={t}>#{t}</span>)}
        </div>
      </div>
      {a.description && <p className="lede">{a.description}</p>}
      <div className="character-grid">
        <div>
          {glb ? <GlbViewer key={glb} url={glb} label={`${a.code} on its reference body`} />
            : <div className="viewer viewer-empty">{v ? "This version has no 3D preview yet." : ""}</div>}
          {onRef && (
            <figure className="pose-sheet">
              <img src={onRef} alt={`${a.name} attached to the reference body`} loading="lazy" />
              <figcaption>Attached to the reference body</figcaption>
            </figure>
          )}
        </div>
        <aside>
          <h2>Versions</h2>
          <VersionTimeline versions={a.versions} selected={vid} recommended={a.recommended_version_id}
            onSelect={(x) => setParams({ version: x })} />
          {v && (v.status === "published" || v.status === "deprecated") && (
            <p><Link to={`/scenes?uses_version_id=${v.id}`}>Scenes that use this version</Link></p>
          )}
        </aside>
      </div>
      <ErrorNote error={version.error} />
      {v && <AccessoryVersionPanel key={v.id} accessory={a} version={v} />}
    </main>
  );
}
