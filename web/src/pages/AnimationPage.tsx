import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { animationFileUrl } from "../api/client";
import { useAnimation, useAnimationVersion } from "../api/hooks";
import { AnimationVersionPanel } from "../components/AnimationVersionPanel";
import { ErrorNote } from "../components/bits";
import { VersionTimeline } from "../components/VersionTimeline";

export function AnimationPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const animation = useAnimation(id);
  const a = animation.data;
  const defaultVersion = useMemo(() => {
    if (!a) return undefined;
    const published = a.versions.filter((v) => v.status === "published");
    return a.recommended_version_id ?? published[0]?.id ?? a.versions[0]?.id;
  }, [a]);
  const vid = params.get("version") ?? defaultVersion;
  const version = useAnimationVersion(id, vid);
  const v = version.data;
  const [strip, setStrip] = useState<string>();
  useEffect(() => {
    setStrip(undefined);
    if (v?.files.some((f) => f.name === "frames.png")) animationFileUrl(id, v.id, "frames.png").then(setStrip);
  }, [id, v?.id, v?.files.length]); // eslint-disable-line react-hooks/exhaustive-deps

  if (animation.error) return <main><ErrorNote error={animation.error} /></main>;
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
          {strip ? (
            <figure className="pose-sheet">
              <img src={strip} alt={`${a.name}: frames across the clip on the reference body`} loading="lazy" />
              <figcaption>Frames across the clip on the reference body</figcaption>
            </figure>
          ) : <div className="viewer viewer-empty">{v ? "This version has not been built yet." : ""}</div>}
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
      {v && <AnimationVersionPanel key={v.id} animation={a} version={v} />}
    </main>
  );
}
