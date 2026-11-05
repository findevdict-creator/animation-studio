// Series and the episode editor (SRS 13 to 18, 22.5). A series casts published characters with
// their default outfit and voice. The episode editor goes script -> storyboard -> voices ->
// episode version (one scene version per shot, exact pins) -> build, reopen, lock -> render.
// The AI only suggests: its script or shots are shown for a person to keep (saved as a
// version) or drop.
import { useEffect, useMemo, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api, type EnvironmentDetail, type EnvironmentOut, type EnvironmentVersionDetail, type EpisodeOut, type EpisodeVersionOut,
  type JobOut, type RenderOut, type ScriptSaved, type SeriesOut, type ShotJobs, type StoryboardOut, type StoryboardSaved,
  type VoiceOut,
} from "../api/client";
import {
  useAudioClips, useCan, useEpisode, useEpisodeAction, useEpisodes, useEpisodeVersion, useGallery, useJob, useOutfits,
  useRenders, useSeries, useSeriesList, useSets,
} from "../api/hooks";
import { ErrorNote, Field, JobProgress, StatusChip, when } from "../components/bits";
import { RenderPanel } from "../components/RenderPanel";
import { EMOTIONS, type Names, type Shot, ShotCard, StoryboardStrip, totalSeconds } from "../components/Storyboard";

// ================================================================ series
interface MemberDraft { character: string; key: string; role: "main" | "supporting"; outfit: string; voice: string; variant: string;
  pitch: number; rate_wpm: number }

export const blankMember = (): MemberDraft => ({ character: "", key: "", role: "main", outfit: "", voice: "en-us", variant: "",
  pitch: 50, rate_wpm: 160 });

/** A cast row as the API wants it: the outfit "OUTFIT-001@1.0" becomes a pin. */
export function memberBody(m: MemberDraft) {
  const [code, version] = m.outfit.split("@");
  return { character: m.character, key: m.key || m.character.toLowerCase().replace(/[^a-z0-9]+/g, "_"), role: m.role,
    outfit_version: m.outfit ? { code, version: version || "1.0" } : null,
    voice: { provider: "espeak-ng", voice: m.voice, variant: m.variant || null, pitch: m.pitch, rate_wpm: m.rate_wpm } };
}

export function SeriesListPage() {
  const list = useSeriesList();
  const canManage = useCan("series.manage");
  const navigate = useNavigate();
  const chars = useGallery({ status: "published" });
  const outfits = useOutfits({ status: "published" });
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [size, setSize] = useState("1920x1080");
  const [samples, setSamples] = useState(12);
  const [members, setMembers] = useState<MemberDraft[]>([blankMember()]);
  const create = useEpisodeAction(() => {
    const [width, height] = size.split("x").map(Number);
    return api<SeriesOut>("/series", { method: "POST", json: { name, description, settings: { render: { width, height, samples } },
      members: members.filter((m) => m.character).map(memberBody) } });
  });
  const setM = (i: number, patch: Partial<MemberDraft>) => setMembers(members.map((m, j) => (j === i ? { ...m, ...patch } : m)));
  return (
    <main>
      <div className="page-head"><h1>Series</h1></div>
      <ErrorNote error={list.error} />
      <ul className="gallery">
        {list.data?.items.map((s) => (
          <li key={s.id}><Link to={`/series/${s.code}`} className="card"><div className="card-body">
            <strong>{s.name}</strong><small>{s.code} · {s.members.length} characters · {s.episodes} episode(s)</small>
            <small>{s.description}</small></div></Link></li>
        ))}
      </ul>
      {list.data && list.data.items.length === 0 && <p className="empty">No series yet.</p>}
      {canManage && (
        <form className="panel" aria-label="new series" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined, {
          onSuccess: (s) => navigate(`/series/${(s as SeriesOut).code}`) }); }}>
          <h2>New series</h2>
          <Field label="Name"><input required maxLength={120} value={name} onChange={(e) => setName(e.target.value)} aria-label="series name" /></Field>
          <Field label="Description"><textarea value={description} onChange={(e) => setDescription(e.target.value)} aria-label="series description" /></Field>
          <div className="row">
            <Field label="Final render size">
              <select value={size} onChange={(e) => setSize(e.target.value)} aria-label="render size">
                <option value="1920x1080">1920×1080</option><option value="1280x720">1280×720</option><option value="640x360">640×360 (drafts)</option>
              </select>
            </Field>
            <Field label="Cycles samples"><input type="number" min={1} max={4096} value={samples} onChange={(e) => setSamples(Number(e.target.value))} aria-label="samples" /></Field>
          </div>
          <strong>Cast</strong>
          {members.map((m, i) => (
            <div className="row" key={i}>
              <Field label="Character">
                <select value={m.character} aria-label={`character ${i + 1}`} onChange={(e) => setM(i, { character: e.target.value })}>
                  <option value="">choose…</option>
                  {chars.data?.items.map((c) => <option key={c.id} value={c.code}>{c.name} ({c.code})</option>)}
                </select>
              </Field>
              <Field label="Key"><input value={m.key} placeholder="ali" pattern="[a-z][a-z0-9_]{0,31}" aria-label={`key ${i + 1}`}
                onChange={(e) => setM(i, { key: e.target.value })} /></Field>
              <Field label="Role">
                <select value={m.role} aria-label={`role ${i + 1}`} onChange={(e) => setM(i, { role: e.target.value as MemberDraft["role"] })}>
                  <option value="main">main</option><option value="supporting">supporting</option>
                </select>
              </Field>
              <Field label="Default outfit">
                <select value={m.outfit} aria-label={`outfit ${i + 1}`} onChange={(e) => setM(i, { outfit: e.target.value })}>
                  <option value="">none</option>
                  {outfits.data?.items.filter((o) => o.latest_published).map((o) => (
                    <option key={o.id} value={`${o.code}@${o.latest_published}`}>{o.name} ({o.code}@{o.latest_published})</option>))}
                </select>
              </Field>
              <Field label="Voice"><input value={m.voice} aria-label={`voice ${i + 1}`} onChange={(e) => setM(i, { voice: e.target.value })} /></Field>
              <Field label="Variant"><input value={m.variant} placeholder="m3, f3…" aria-label={`variant ${i + 1}`} onChange={(e) => setM(i, { variant: e.target.value })} /></Field>
              <Field label="Pitch"><input type="number" min={0} max={99} value={m.pitch} aria-label={`pitch ${i + 1}`} onChange={(e) => setM(i, { pitch: Number(e.target.value) })} /></Field>
            </div>
          ))}
          <div className="row">
            <button type="button" onClick={() => setMembers([...members, blankMember()])}>Add character</button>
            <button className="primary" disabled={!name || create.isPending}>Create series</button>
          </div>
          <ErrorNote error={create.error} />
        </form>
      )}
    </main>
  );
}

export function SeriesPage() {
  const { ref } = useParams();
  const series = useSeries(ref);
  const episodes = useEpisodes(ref);
  const canEpisode = useCan("episode.manage");
  const navigate = useNavigate();
  const [title, setTitle] = useState("");
  const [concept, setConcept] = useState("");
  const create = useEpisodeAction(() => api<EpisodeOut>("/episodes", { method: "POST", json: { series_id: ref, title, concept } }));
  const s = series.data;
  if (!s) return <main><ErrorNote error={series.error} />loading…</main>;
  const render = (s.settings as { render: Record<string, unknown> }).render;
  return (
    <main>
      <div className="page-head"><h1>{s.name}</h1><small>{s.code} · style {s.style_profile_ref}</small></div>
      <p>{s.description}</p>
      <section className="panel">
        <h2>Cast</h2>
        <table className="files">
          <thead><tr><th>Character</th><th>Key</th><th>Role</th><th>Voice</th></tr></thead>
          <tbody>{s.members.map((m) => {
            const v = m.voice as { provider: string; voice: string; variant?: string; pitch: number; rate_wpm: number };
            return <tr key={m.id}><td><Link to={`/characters/${m.character_id}`}>{m.character_name}</Link> <small>{m.character_code}</small></td>
              <td>{m.key}</td><td>{m.role}</td><td><small>{v.provider} {v.voice}{v.variant ? `+${v.variant}` : ""} · pitch {v.pitch} · {v.rate_wpm} wpm</small></td></tr>;
          })}</tbody>
        </table>
        <small>Renders at {String(render.width)}×{String(render.height)}, {String(render.fps)} fps, {String(render.engine)} {String(render.samples)} samples, {String(render.view_transform)}</small>
        {s.assets.length > 0 && <div className="tags">{s.assets.map((a) => <span key={a.asset_version_id} className="chip">{a.display}</span>)}</div>}
      </section>
      <section className="panel">
        <h2>Episodes</h2>
        <ul>{episodes.data?.items.map((e) => <li key={e.id}><Link to={`/episodes/${e.code}`}>{e.number}. {e.title}</Link> <small>{e.code}</small></li>)}</ul>
        {canEpisode && (
          <form className="row" aria-label="new episode" onSubmit={(e) => { e.preventDefault(); create.mutate(undefined, {
            onSuccess: (ep) => navigate(`/episodes/${(ep as EpisodeOut).code}`) }); }}>
            <Field label="Title"><input required value={title} onChange={(e) => setTitle(e.target.value)} aria-label="episode title" /></Field>
            <Field label="Concept"><input className="grow" value={concept} onChange={(e) => setConcept(e.target.value)} aria-label="episode concept" /></Field>
            <button className="primary" disabled={!title || create.isPending}>New episode</button>
          </form>
        )}
        <ErrorNote error={create.error} />
      </section>
    </main>
  );
}

// ================================================================ script editor
export interface ScriptLine { character_id: string; text: string; emotion?: string | null }
export interface ScriptEvent { character_id: string; action: string; target?: string | null }
export interface ScriptScene { scene_id: string; location_id?: string | null; description: string; emotional_context?: string;
  characters: string[]; events: ScriptEvent[]; dialogue: ScriptLine[] }
export interface Script { title: string; synopsis: string; scenes: ScriptScene[] }

export const blankScene = (n: number, cast: string[]): ScriptScene => ({ scene_id: `SC-${String(n).padStart(3, "0")}`,
  location_id: null, description: "", characters: cast.slice(0, 2), events: [], dialogue: [] });

/** What is sent: empty lines dropped, scene ids renumbered in order. */
export function cleanScript(s: Script): Script {
  return { ...s, scenes: s.scenes.map((sc, i) => ({ ...sc, scene_id: `SC-${String(i + 1).padStart(3, "0")}`,
    location_id: sc.location_id || null, dialogue: sc.dialogue.filter((d) => d.text.trim()).map((d) => ({ ...d, emotion: d.emotion || null })) })) };
}

function ScriptEditor({ script, onChange, names, locations, readOnly }: { script: Script; onChange: (s: Script) => void; names: Names;
  locations: EnvironmentOut[]; readOnly: boolean }) {
  const cast = Object.keys(names);
  const setScene = (i: number, patch: Partial<ScriptScene>) => onChange({ ...script, scenes: script.scenes.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  return (
    <fieldset disabled={readOnly} className="script">
      <Field label="Title"><input value={script.title} onChange={(e) => onChange({ ...script, title: e.target.value })} aria-label="script title" /></Field>
      <Field label="Synopsis"><textarea value={script.synopsis} onChange={(e) => onChange({ ...script, synopsis: e.target.value })} aria-label="synopsis" /></Field>
      {script.scenes.map((sc, i) => (
        <div className="panel" key={i} aria-label={`scene ${i + 1}`}>
          <div className="panel-head"><h3>{sc.scene_id}</h3>
            <select value={sc.location_id ?? ""} aria-label={`scene ${i + 1} location`} onChange={(e) => setScene(i, { location_id: e.target.value || null })}>
              <option value="">no location</option>
              {locations.map((l) => <option key={l.id} value={l.code}>{l.name} ({l.code})</option>)}
            </select>
            {!readOnly && <button type="button" onClick={() => onChange({ ...script, scenes: script.scenes.filter((_, j) => j !== i) })}>remove scene</button>}
          </div>
          <Field label="What happens"><input value={sc.description} onChange={(e) => setScene(i, { description: e.target.value })} aria-label={`scene ${i + 1} description`} /></Field>
          <div className="row">{cast.map((c) => (
            <label key={c}><input type="checkbox" checked={sc.characters.includes(c)} aria-label={`scene ${i + 1} has ${names[c]}`}
              onChange={(e) => setScene(i, { characters: e.target.checked ? [...sc.characters, c] : sc.characters.filter((x) => x !== c) })} /> {names[c]}</label>
          ))}</div>
          {sc.dialogue.map((ln, k) => (
            <div className="row" key={k}>
              <select value={ln.character_id} aria-label={`scene ${i + 1} line ${k + 1} speaker`} onChange={(e) => setScene(i, {
                dialogue: sc.dialogue.map((d, j) => (j === k ? { ...d, character_id: e.target.value } : d)) })}>
                {sc.characters.map((c) => <option key={c} value={c}>{names[c] ?? c}</option>)}
              </select>
              <input className="grow" value={ln.text} maxLength={500} aria-label={`scene ${i + 1} line ${k + 1}`} onChange={(e) => setScene(i, {
                dialogue: sc.dialogue.map((d, j) => (j === k ? { ...d, text: e.target.value } : d)) })} />
              <select value={ln.emotion ?? ""} aria-label={`scene ${i + 1} line ${k + 1} emotion`} onChange={(e) => setScene(i, {
                dialogue: sc.dialogue.map((d, j) => (j === k ? { ...d, emotion: e.target.value || null } : d)) })}>
                <option value="">—</option>{EMOTIONS.filter((x) => x !== "neutral").map((x) => <option key={x} value={x}>{x}</option>)}
              </select>
            </div>
          ))}
          {!readOnly && sc.characters.length > 0 && <button type="button" onClick={() => setScene(i, {
            dialogue: [...sc.dialogue, { character_id: sc.characters[0], text: "" }] })}>Add line</button>}
        </div>
      ))}
      {!readOnly && <button type="button" onClick={() => onChange({ ...script, scenes: [...script.scenes, blankScene(script.scenes.length + 1, cast)] })}>Add scene</button>}
    </fieldset>
  );
}

// ================================================================ environment layouts
type Layout = { marks: { name: string; kind?: string }[]; cameras: { name: string }[] };

function useLayouts(codes: string[]): Record<string, Layout> {
  const envs = useSets<EnvironmentOut>("environments", { status: "published" });
  const wanted = (envs.data?.items ?? []).filter((e) => codes.includes(e.code));
  const details = useQueries({ queries: wanted.map((e) => ({ queryKey: ["environments", "one", e.id],
    queryFn: () => api<EnvironmentDetail>(`/environments/${e.id}`) })) });
  const versions = useQueries({ queries: details.map((d) => {
    const det = d.data;
    const v = det && (det.versions.find((x) => x.id === det.recommended_version_id)
      ?? [...det.versions].reverse().find((x) => x.status === "published"));
    return { queryKey: ["environments", "version", det?.id, v?.id], enabled: !!v,
      queryFn: () => api<EnvironmentVersionDetail>(`/environments/${det!.id}/versions/${v!.id}`) };
  }) });
  const out: Record<string, Layout> = {};
  versions.forEach((q, i) => { if (q.data?.layout && wanted[i]) out[wanted[i].code] = q.data.layout as unknown as Layout; });
  return out;
}

// ================================================================ episode editor
type Tab = "script" | "storyboard" | "version" | "render";

export function EpisodePage() {
  const { ref } = useParams();
  const episode = useEpisode(ref);
  const ep = episode.data;
  const series = useSeries(ep?.series_code);
  const [tab, setTab] = useState<Tab>("script");
  if (!ep) return <main><ErrorNote error={episode.error} />loading…</main>;
  const names: Names = Object.fromEntries((series.data?.members ?? []).map((m) => [m.character_code, m.character_name]));
  return (
    <main>
      <div className="page-head">
        <h1>{ep.title}</h1>
        <small>{ep.code} · episode {ep.number} of <Link to={`/series/${ep.series_code}`}>{series.data?.name ?? ep.series_code}</Link></small>
      </div>
      <p>{ep.concept}</p>
      <nav className="tabs" role="tablist">
        {(["script", "storyboard", "version", "render"] as Tab[]).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
            {{ script: `Script (${ep.script_versions.length})`, storyboard: `Storyboard (${ep.storyboards.length})`,
              version: `Episode versions (${ep.versions.length})`, render: "Render" }[t]}</button>
        ))}
      </nav>
      {tab === "script" && <ScriptTab ep={ep} names={names} />}
      {tab === "storyboard" && <StoryboardTab ep={ep} names={names} />}
      {tab === "version" && <VersionTab ep={ep} />}
      {tab === "render" && <RenderTab ep={ep} />}
    </main>
  );
}

function AiResult({ jobId, onKeep }: { jobId: string; onKeep: (data: { spec: unknown; ai_generation_id: string }) => void }) {
  const job = useJob(jobId);
  const data = (job.data?.result as { data?: { spec: unknown; ai_generation_id: string; notes?: string } } | null)?.data;
  return (
    <div className="panel">
      <JobProgress id={jobId} />
      {data && (<>
        <small>The AI suggests this ({data.notes}). Nothing is saved until you keep it.</small>
        <button className="primary" onClick={() => onKeep(data)}>Load the suggestion into the editor</button>
      </>)}
    </div>
  );
}

function ScriptTab({ ep, names }: { ep: EpisodeOut; names: Names }) {
  const canEdit = useCan("episode.manage");
  const locations = useSets<EnvironmentOut>("environments", { status: "published" }).data?.items ?? [];
  const latest = ep.script_versions[ep.script_versions.length - 1];
  const [shown, setShown] = useState<string | undefined>(latest?.id);
  const base = ep.script_versions.find((v) => v.id === shown) ?? latest;
  const [draft, setDraft] = useState<Script>(() => (base?.spec as unknown as Script) ?? { title: ep.title, synopsis: "", scenes: [blankScene(1, Object.keys(names))] });
  const [aiGen, setAiGen] = useState<string | null>(null);
  const [aiJob, setAiJob] = useState<string>();
  useEffect(() => { if (base) setDraft(base.spec as unknown as Script); }, [base?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = useEpisodeAction(() => api<ScriptSaved>(`/episodes/${ep.code}/scripts`, { method: "POST", json: {
    spec: cleanScript(draft), based_on_id: base?.id ?? null, ai_generation_id: aiGen } }));
  const generate = useEpisodeAction(() => api<JobOut>(`/episodes/${ep.code}/scripts/generate`, { method: "POST", json: {},
    idempotencyKey: crypto.randomUUID() }));
  return (
    <section className="panel" aria-label="script">
      <div className="panel-head">
        <h2>Script</h2>
        <select value={base?.id ?? ""} onChange={(e) => setShown(e.target.value)} aria-label="script version">
          {ep.script_versions.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.source} · {when(v.created_at)}</option>)}
          {!ep.script_versions.length && <option value="">no version yet</option>}
        </select>
        <span className="spacer" />
        {canEdit && <button onClick={() => generate.mutate(undefined, { onSuccess: (j) => setAiJob((j as JobOut).id) })}
          disabled={generate.isPending}>Ask the AI for a script</button>}
      </div>
      {aiJob && <AiResult jobId={aiJob} onKeep={(d) => { setDraft(d.spec as Script); setAiGen(d.ai_generation_id); setAiJob(undefined); }} />}
      {aiGen && <small className="chip">editing an AI suggestion; it is saved with its generation log</small>}
      <ScriptEditor script={draft} onChange={setDraft} names={names} locations={locations} readOnly={!canEdit} />
      <ErrorNote error={save.error ?? generate.error} />
      {canEdit && <button className="primary" onClick={() => save.mutate(undefined, { onSuccess: (r) => {
        setShown((r as ScriptSaved).version.id); setAiGen(null); } })} disabled={save.isPending}>Save as a new script version</button>}
    </section>
  );
}

function StoryboardTab({ ep, names }: { ep: EpisodeOut; names: Names }) {
  const canEdit = useCan("episode.manage");
  const latest = ep.storyboards[ep.storyboards.length - 1];
  const [shown, setShown] = useState<string | undefined>(latest?.id);
  const sb: StoryboardOut | undefined = ep.storyboards.find((s) => s.id === shown) ?? latest;
  const [shots, setShots] = useState<Shot[]>((sb?.shots as unknown as Shot[]) ?? []);
  const [sel, setSel] = useState(0);
  const [aiJob, setAiJob] = useState<string>();
  const [aiGen, setAiGen] = useState<string | null>(null);
  const [voiceJobs, setVoiceJobs] = useState<JobOut[]>([]);
  useEffect(() => { setShots((sb?.shots as unknown as Shot[]) ?? []); setSel(0); setAiGen(null); }, [sb?.id]);
  const sv = ep.script_versions[ep.script_versions.length - 1];
  const layouts = useLayouts([...new Set(shots.map((s) => s.environment).filter(Boolean) as string[])]);
  const done = (r: unknown) => setShown((r as StoryboardSaved).storyboard.id);
  const plan = useEpisodeAction(() => api<StoryboardSaved>(`/episodes/${ep.code}/storyboards`, { method: "POST", json: { script_version_id: sv!.id } }));
  const save = useEpisodeAction(() => api<StoryboardSaved>(`/episodes/${ep.code}/storyboards`, { method: "POST", json: {
    script_version_id: sb?.script_version_id ?? sv!.id, source: aiGen ? "ai" : "manual", spec: { shots }, based_on_id: sb?.id ?? null,
    ai_generation_id: aiGen } }));
  const suggest = useEpisodeAction(() => api<JobOut>(`/episodes/${ep.code}/storyboards/suggest`, { method: "POST",
    json: { script_version_id: sv!.id }, idempotencyKey: crypto.randomUUID() }));
  const voice = useEpisodeAction(() => api<VoiceOut>(`/storyboards/${sb!.id}/voice`, { method: "POST" }));
  const voiced = useEpisodeAction(() => api<StoryboardSaved>(`/storyboards/${sb!.id}/voiced`, { method: "POST" }));
  const clips = useAudioClips();
  const dirty = JSON.stringify(shots) !== JSON.stringify(sb?.shots ?? []);
  const unvoiced = shots.reduce((n, s) => n + s.dialogue.filter((d) => !d.audio).length, 0);
  const shot = shots[sel];
  const lay = shot?.environment ? layouts[shot.environment] : undefined;
  if (!sv) return <section className="panel"><p className="empty">Save a script version first.</p></section>;
  return (
    <section className="panel" aria-label="storyboard">
      <div className="panel-head">
        <h2>Storyboard</h2>
        <select value={sb?.id ?? ""} onChange={(e) => setShown(e.target.value)} aria-label="storyboard version">
          {ep.storyboards.map((s) => <option key={s.id} value={s.id}>#{s.number} · {s.source} · {s.duration_s.toFixed(1)} s</option>)}
          {!ep.storyboards.length && <option value="">none yet</option>}
        </select>
        <span className="spacer" />
        {canEdit && <button onClick={() => plan.mutate(undefined, { onSuccess: done })}>Plan from script v{sv.number}</button>}
        {canEdit && <button onClick={() => suggest.mutate(undefined, { onSuccess: (j) => setAiJob((j as JobOut).id) })}>Ask the AI for shots</button>}
      </div>
      {aiJob && <AiResult jobId={aiJob} onKeep={(d) => { setShots((d.spec as { shots: Shot[] }).shots); setAiGen(d.ai_generation_id); setAiJob(undefined); }} />}
      {shots.length > 0 && <>
        <StoryboardStrip shots={shots} names={names} selected={sel} onSelect={setSel} />
        <small>{shots.length} shots · {totalSeconds(shots).toFixed(1)} s{dirty ? " · unsaved changes" : ""}{unvoiced ? ` · ${unvoiced} line(s) not voiced` : ""}</small>
        {shot && <ShotCard shot={shot} names={names} readOnly={!canEdit}
          marks={(lay?.marks ?? []).filter((m) => (m.kind ?? "spawn") !== "seat").map((m) => m.name)}
          cameras={(lay?.cameras ?? []).map((c) => c.name)}
          onChange={(s) => setShots(shots.map((x, i) => (i === sel ? s : x)))} />}
      </>}
      <ErrorNote error={plan.error ?? save.error ?? suggest.error ?? voice.error ?? voiced.error} />
      {canEdit && <div className="row">
        <button className="primary" disabled={!dirty || save.isPending} onClick={() => save.mutate(undefined, { onSuccess: done })}>Save as a new storyboard</button>
        <button disabled={!sb || dirty || !unvoiced || voice.isPending} onClick={() => voice.mutate(undefined, {
          onSuccess: (r) => setVoiceJobs((r as VoiceOut).jobs) })}>Speak the lines</button>
        <button disabled={!sb || dirty || !unvoiced || voiced.isPending} onClick={() => voiced.mutate(undefined, { onSuccess: done })}>
          Make the voiced storyboard</button>
      </div>}
      {voiceJobs.length > 0 && <div className="row">{voiceJobs.map((j) => <JobProgress key={j.id} id={j.id}
        onDone={() => clips.refetch()} />)}</div>}
    </section>
  );
}

function VersionTab({ ep }: { ep: EpisodeOut }) {
  const canEdit = useCan("episode.manage");
  const canScene = useCan("scene.edit");
  const latest = ep.versions[ep.versions.length - 1];
  const [shown, setShown] = useState<string | undefined>(latest?.id);
  const v = useEpisodeVersion(shown ?? latest?.id);
  const voicedBoards = ep.storyboards.filter((s) => s.source === "voiced");
  const [board, setBoard] = useState<string>((voicedBoards[voicedBoards.length - 1] ?? ep.storyboards[ep.storyboards.length - 1])?.id ?? "");
  const music = useAudioClips({ kind: "music", status: "ready" });
  const [musicCode, setMusicCode] = useState("");
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const create = useEpisodeAction(() => api<EpisodeVersionOut>(`/episodes/${ep.code}/versions`, { method: "POST", json: {
    storyboard_id: board, music: musicCode ? [{ audio: musicCode, volume: 0.25, fade_in_s: 1, fade_out_s: 1.5 }] : [] } }));
  const act = (what: "build" | "reopen" | "lock") => api<ShotJobs | EpisodeVersionOut>(`/episode-versions/${v.data!.id}/${what}`,
    { method: "POST", json: what === "lock" ? undefined : {} });
  const build = useEpisodeAction(() => act("build"));
  const reopen = useEpisodeAction(() => act("reopen"));
  const lock = useEpisodeAction(() => act("lock"));
  const d = v.data;
  return (
    <section className="panel" aria-label="episode versions">
      <div className="panel-head">
        <h2>Episode versions</h2>
        <select value={shown ?? latest?.id ?? ""} onChange={(e) => setShown(e.target.value)} aria-label="episode version">
          {ep.versions.map((x) => <option key={x.id} value={x.id}>v{x.number} · {x.status} · {x.duration_s.toFixed(1)} s</option>)}
          {!ep.versions.length && <option value="">none yet</option>}
        </select>
      </div>
      {canEdit && ep.storyboards.length > 0 && (
        <div className="row">
          <Field label="From storyboard">
            <select value={board} onChange={(e) => setBoard(e.target.value)} aria-label="from storyboard">
              {ep.storyboards.map((s) => <option key={s.id} value={s.id}>#{s.number} · {s.source}</option>)}
            </select>
          </Field>
          <Field label="Music">
            <select value={musicCode} onChange={(e) => setMusicCode(e.target.value)} aria-label="music">
              <option value="">none</option>
              {music.data?.items.filter((c) => c.status === "ready").map((c) => <option key={c.id} value={c.code}>{c.name} ({c.code})</option>)}
            </select>
          </Field>
          <button className="primary" disabled={!board || create.isPending} onClick={() => create.mutate(undefined, {
            onSuccess: (r) => setShown((r as EpisodeVersionOut).id) })}>Make an episode version</button>
        </div>
      )}
      <ErrorNote error={create.error ?? build.error ?? reopen.error ?? lock.error} />
      {d && (<>
        <div className="row">
          <StatusChip status={d.status} />
          <small>{d.shots.length} shots · {d.duration_s.toFixed(1)} s · {(d.progress as { build: Record<string, number> }).build.built ?? 0} built ·
            {" "}{(d.progress as { verify: Record<string, number> }).verify.verified ?? 0} reopened and checked{d.locked_at ? ` · locked ${when(d.locked_at)}` : ""}</small>
          <span className="spacer" />
          {canScene && <button onClick={() => build.mutate(undefined, { onSuccess: (r) => setJobs((r as ShotJobs).jobs) })}>Build shots</button>}
          {canScene && <button onClick={() => reopen.mutate(undefined, { onSuccess: (r) => setJobs((r as ShotJobs).jobs) })}>Reopen and check</button>}
          {canEdit && d.status === "draft" && <button className="primary" onClick={() => lock.mutate(undefined)}>Lock</button>}
        </div>
        {jobs.length > 0 && <small>{jobs.length} job(s) queued</small>}
        <table className="files">
          <thead><tr><th>Shot</th><th>Scene</th><th>Starts</th><th>Length</th><th>In</th><th>Build</th><th>Reopen</th></tr></thead>
          <tbody>{d.shots.map((s) => (
            <tr key={s.shot_id}>
              <td>{s.shot_id}</td>
              <td><Link to={`/scenes/${s.scene_id}`}>{s.scene_code} v{s.scene_version}</Link></td>
              <td>{s.start_s.toFixed(2)} s</td><td>{s.duration_s.toFixed(2)} s</td>
              <td>{(s.transition as { kind: string }).kind}</td>
              <td><StatusChip status={`build-${s.build_status}`} /></td><td><StatusChip status={`reopen-${s.verify_status}`} /></td>
            </tr>
          ))}</tbody>
        </table>
        <details>
          <summary>Pinned versions ({d.closure.length}) and audio ({d.audio.length})</summary>
          <div className="tags">{d.closure.map((c) => <span key={c.asset_version_id} className="chip">{c.display}</span>)}</div>
          <ul>{d.audio.map((a) => <li key={a.code}>{a.code} · {a.role} · {a.source}{a.text ? ` “${a.text}”` : ""}</li>)}</ul>
        </details>
      </>)}
    </section>
  );
}

function RenderTab({ ep }: { ep: EpisodeOut }) {
  const canRender = useCan("render.submit");
  const renders = useRenders(ep.code);
  const locked = ep.versions.filter((v) => v.status === "locked");
  const [version, setVersion] = useState(locked[locked.length - 1]?.id ?? "");
  const [samples, setSamples] = useState<number | "">("");
  const create = useEpisodeAction(() => api<RenderOut>("/render-jobs", { method: "POST", json: {
    episode_version_id: version, settings: samples === "" ? {} : { samples } } }));
  const byVersion = useMemo(() => Object.fromEntries(ep.versions.map((v) => [v.id, v.number])), [ep.versions]);
  return (
    <section className="panel" aria-label="render">
      <h2>Render</h2>
      {locked.length === 0 && <p className="empty">Lock an episode version to render it.</p>}
      {canRender && locked.length > 0 && (
        <div className="row">
          <Field label="Locked version">
            <select value={version} onChange={(e) => setVersion(e.target.value)} aria-label="locked version">
              {locked.map((v) => <option key={v.id} value={v.id}>v{v.number} · {v.duration_s.toFixed(1)} s</option>)}
            </select>
          </Field>
          <Field label="Samples (blank: the series')">
            <input type="number" min={1} max={4096} value={samples} aria-label="render samples"
              onChange={(e) => setSamples(e.target.value === "" ? "" : Number(e.target.value))} />
          </Field>
          <button className="primary" disabled={!version || create.isPending} onClick={() => create.mutate(undefined)}>Render</button>
        </div>
      )}
      <ErrorNote error={create.error ?? renders.error} />
      {renders.data?.items.map((r) => (
        <div key={r.id}><small>episode version v{byVersion[r.episode_version_id]}</small><RenderPanel render={r} canRun={canRender} /></div>
      ))}
    </section>
  );
}
