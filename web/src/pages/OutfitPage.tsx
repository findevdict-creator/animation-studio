import { useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { FitOut, outfitFileUrl } from "../api/client";
import { useFit, useOutfit, useOutfitVersion } from "../api/hooks";
import { ErrorNote } from "../components/bits";
import { FitReport } from "../components/FitReport";
import { FitsPanel } from "../components/FitsPanel";
import { GlbViewer } from "../components/GlbViewer";
import { OutfitVersionPanel } from "../components/OutfitVersionPanel";
import { VersionTimeline } from "../components/VersionTimeline";

export function OutfitPage() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const outfit = useOutfit(id);
  const o = outfit.data;
  const defaultVersion = useMemo(() => {
    if (!o) return undefined;
    const published = o.versions.filter((v) => v.status === "published");
    return o.recommended_version_id ?? published[0]?.id ?? o.versions[0]?.id;
  }, [o]);
  const vid = params.get("version") ?? defaultVersion;
  const version = useOutfitVersion(id, vid);
  const v = version.data;
  const [fit, setFit] = useState<FitOut>();
  const fitDetail = useFit(id, vid, fit?.character_version_id);
  const [glb, setGlb] = useState<string>();
  const [poses, setPoses] = useState<string>();
  useEffect(() => setFit(undefined), [vid]);
  useEffect(() => {
    setGlb(undefined);
    setPoses(undefined);
    if (fit) {
      setGlb(fit.files.find((f) => f.name === "preview.glb")?.url);
      setPoses(fit.files.find((f) => f.name === "poses.png")?.url);
    } else if (v) {
      if (v.files.some((f) => f.name === "preview.glb")) outfitFileUrl(id, v.id, "preview.glb").then(setGlb);
      if (v.files.some((f) => f.name === "poses.png")) outfitFileUrl(id, v.id, "poses.png").then(setPoses);
    }
  }, [id, v?.id, v?.files.length, fit?.id, fit?.checked_at]); // eslint-disable-line react-hooks/exhaustive-deps

  if (outfit.error) return <main><ErrorNote error={outfit.error} /></main>;
  if (!o) return <main>loading…</main>;
  const wearer = fit ? `${fit.character.name} v${fit.character.version}` : `reference body (${v?.primary_fit_profile ?? ""})`;
  return (
    <main className="character">
      <div className="page-head">
        <h1>{o.name} <small>{o.code}</small></h1>
        <div className="tags">
          <span className="chip">{o.scope.replace("_", " ")}</span>
          {o.tags.map((t) => <span key={t}>#{t}</span>)}
        </div>
      </div>
      {o.description && <p className="lede">{o.description}</p>}
      <div className="character-grid">
        <div>
          {glb ? <GlbViewer key={glb} url={glb} label={`${o.code} on ${wearer}`} />
            : <div className="viewer viewer-empty">{v ? "This version has no try-on preview yet." : ""}</div>}
          {poses && (
            <figure className="pose-sheet">
              <img src={poses} alt={`fit-check poses on ${wearer}`} loading="lazy" />
              <figcaption>Fit-check poses on {wearer}</figcaption>
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
      {v && fit && <section className="panel"><FitReport report={fitDetail.data?.report} /></section>}
      {v && <FitsPanel outfit={o} version={v} selected={fit?.character_version_id} onSelect={setFit} />}
      {v && <OutfitVersionPanel key={v.id} outfit={o} version={v} />}
    </main>
  );
}
