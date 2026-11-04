import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { fileUrl } from "../api/client";
import { useCharacter, useVersion } from "../api/hooks";
import { ErrorNote } from "../components/bits";
import { GlbViewer } from "../components/GlbViewer";
import { VersionPanel } from "../components/VersionPanel";
import { VersionTimeline } from "../components/VersionTimeline";

export function CharacterPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const character = useCharacter(id);
  const c = character.data;
  const defaultVersion = useMemo(() => {
    if (!c) return undefined;
    const published = c.versions.filter((v) => v.status === "published");
    return c.recommended_version_id ?? published[0]?.id ?? c.versions[0]?.id;
  }, [c]);
  const vid = params.get("version") ?? defaultVersion;
  const version = useVersion(id, vid);
  const v = version.data;
  const [glb, setGlb] = useState<string>();
  useEffect(() => {
    setGlb(undefined);
    if (v && v.files.some((f) => f.name === "preview.glb")) fileUrl(id, v.id, "preview.glb").then(setGlb);
  }, [id, v?.id, v?.files.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (character.error) return <main><ErrorNote error={character.error} /></main>;
  if (!c) return <main>loading…</main>;
  return (
    <main className="character">
      <div className="page-head">
        <h1>{c.name} <small>{c.code}</small></h1>
        <div className="tags">{c.tags.map((t) => <span key={t}>#{t}</span>)}</div>
      </div>
      {c.description && <p className="lede">{c.description}</p>}
      <div className="character-grid">
        <div>
          {glb ? <GlbViewer url={glb} label={v?.version_label ? `${c.code} v${v.version_label}` : `${c.code} draft`} />
            : <div className="viewer viewer-empty">{v ? "This version has no preview.glb yet." : ""}</div>}
        </div>
        <aside>
          <h2>Versions</h2>
          <VersionTimeline versions={c.versions} selected={vid} recommended={c.recommended_version_id}
            onSelect={(x) => setParams({ version: x })} />
        </aside>
      </div>
      <ErrorNote error={version.error} />
      {v && <VersionPanel key={v.id} character={c} version={v} />}
    </main>
  );
}
