// A render job (SRS 18.2): its status and progress, the settings and exact asset versions it
// rendered, one job per shot then the export, cancel and retry, the logs, and the video.
import { useState } from "react";
import { api, type RenderOut } from "../api/client";
import { RENDER_DONE, useEpisodeAction, useRenderLogs } from "../api/hooks";
import { bytes, ErrorNote, StatusChip, when } from "./bits";

export function renderSummary(r: Pick<RenderOut, "settings">): string {
  const s = r.settings as Record<string, unknown>;
  return `${s.width}×${s.height} · ${s.fps} fps · ${String(s.engine)} ${s.samples} samples · ${String(s.view_transform)} · ` +
    `${String(s.video_codec)} crf ${s.crf} + ${String(s.audio_codec)} ${s.audio_bitrate_k} kb/s`;
}

export function RenderPanel({ render, canRun }: { render: RenderOut; canRun: boolean }) {
  const [logs, setLogs] = useState(false);
  const logList = useRenderLogs(render.code, logs);
  const cancel = useEpisodeAction(() => api<RenderOut>(`/render-jobs/${render.code}/cancel`, { method: "POST" }));
  const retry = useEpisodeAction(() => api<RenderOut>(`/render-jobs/${render.code}/retry`, { method: "POST" }));
  const p = render.progress as { value: number; shots_done: number; shots: number; frames: number };
  const video = render.outputs.find((o) => o.kind === "video");
  const meta = (video?.meta ?? {}) as { duration_s?: number; video?: { frames: number }; audio?: { codec: string } };
  const done = RENDER_DONE.includes(render.status);
  return (
    <section className="panel" aria-label={`render ${render.code}`}>
      <div className="panel-head">
        <h3>{render.code}</h3>
        <StatusChip status={render.status} />
        <small>{when(render.created_at)}{render.finished_at ? ` → ${when(render.finished_at)}` : ""}</small>
        <span className="spacer" />
        {canRun && !done && <button onClick={() => cancel.mutate(undefined)} disabled={cancel.isPending}>Cancel</button>}
        {canRun && render.status === "failed" && <button className="primary" onClick={() => retry.mutate(undefined)}
          disabled={retry.isPending}>Retry failed jobs</button>}
      </div>
      {!done && <progress value={p.value} max={1} aria-label="render progress" />}
      <small>{p.shots_done}/{p.shots} shots rendered · {p.frames} frames{render.export_job ? ` · export ${render.export_job.status}` : ""}</small>
      <small>{renderSummary(render)}{render.blender_version ? ` · Blender ${render.blender_version}` : ""}</small>
      {render.error && <pre className="job-error">{String((render.error as { message?: string }).message ?? JSON.stringify(render.error))}</pre>}
      <ErrorNote error={cancel.error ?? retry.error} />
      {render.video_url && (
        <figure>
          <video src={render.video_url} controls width={640} aria-label="episode video" />
          <figcaption>{meta.duration_s?.toFixed(2)} s · {meta.video?.frames} frames · {meta.audio?.codec}
            {video?.size_bytes ? ` · ${bytes(video.size_bytes)}` : ""} · <a href={render.video_url} download={`${render.code}.mp4`}>download MP4</a></figcaption>
        </figure>
      )}
      <details>
        <summary>Pinned asset versions ({render.asset_versions.length})</summary>
        <div className="tags">{render.asset_versions.map((a) => <span key={a} className="chip">{a}</span>)}</div>
      </details>
      <table className="files">
        <thead><tr><th>Shot</th><th>Job</th><th>Progress</th><th>Frames</th></tr></thead>
        <tbody>
          {render.shots.map((s) => (
            <tr key={s.job.id}>
              <td>{s.shot_id}</td><td><StatusChip status={s.job.status} /></td>
              <td><small>{Math.round(s.job.progress * 100)}% {s.job.progress_message}</small></td><td>{s.frames ?? ""}</td>
            </tr>
          ))}
          {render.export_job && (
            <tr><td>export</td><td><StatusChip status={render.export_job.status} /></td>
              <td><small>{Math.round(render.export_job.progress * 100)}% {render.export_job.progress_message}</small></td><td /></tr>
          )}
        </tbody>
      </table>
      <details onToggle={(e) => setLogs((e.target as HTMLDetailsElement).open)}>
        <summary>Logs</summary>
        <ul>{logList.data?.map((l) => (
          <li key={l.job_id}>{l.shot_id ?? l.type} · {l.status} · {l.url ? <a href={l.url}>log</a> : "no log"}</li>
        ))}</ul>
      </details>
    </section>
  );
}
