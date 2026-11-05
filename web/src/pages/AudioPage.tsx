// Dialogue recordings for lip sync: upload a recording with its transcript, the media worker
// normalises it and finds the mouth shapes; a ready clip (AUDIO-001) can be put on a
// dialogue line in the scene editor.
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api, upload, type AudioJobOut, type AudioOut } from "../api/client";
import { useAudioClips, useCan } from "../api/hooks";
import { ErrorNote, Field, StatusChip, when } from "../components/bits";

export function CueStrip({ clip }: { clip: AudioOut }) {
  if (!clip.cues || !clip.duration_s) return null;
  const d = clip.duration_s;
  return (
    <div className="cue-strip" role="img" aria-label={`mouth shapes of ${clip.code}`}>
      {clip.cues.map((c, i) => (
        <span key={i} className={`cue cue-${c.viseme}`} style={{ left: `${(c.start_s / d) * 100}%`, width: `${((c.end_s - c.start_s) / d) * 100}%` }}
          title={`${c.start_s.toFixed(2)}–${c.end_s.toFixed(2)} s: ${c.viseme}`}>{c.viseme === "rest" ? "" : c.viseme}</span>
      ))}
    </div>
  );
}

export function AudioPage() {
  const clips = useAudioClips();
  const canCreate = useCan("asset.create");
  const qc = useQueryClient();
  const [file, setFile] = useState<File>();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(undefined);
    try {
      const up = await upload("audio", file);
      await api<AudioJobOut>("/audio-clips", { method: "POST", json: { upload_id: up.id, dialog_text: text || null },
        idempotencyKey: crypto.randomUUID() });
      setFile(undefined);
      setText("");
      qc.invalidateQueries({ queryKey: ["audio-clips"] });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main>
      <div className="page-head"><h1>Dialogue audio</h1></div>
      {canCreate && (
        <form className="new-character" onSubmit={add}>
          <Field label="Recording (WAV, MP3, Ogg, FLAC; up to a minute)">
            <input type="file" accept=".wav,.mp3,.ogg,.flac,.m4a,audio/*" aria-label="recording" onChange={(e) => setFile(e.target.files?.[0])} />
          </Field>
          <Field label="What is said (helps the mouth shapes)">
            <textarea maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} aria-label="transcript" />
          </Field>
          <ErrorNote error={error} />
          <button className="primary" disabled={busy || !file}>Upload and lip-sync</button>
        </form>
      )}
      <ErrorNote error={clips.error} />
      {clips.data && clips.data.items.length === 0 && <p className="empty">No recordings yet.</p>}
      <table className="files">
        <thead><tr><th>Clip</th><th>Status</th><th>Length</th><th>Transcript</th><th>Mouth shapes</th><th>Analyser</th></tr></thead>
        <tbody>
          {clips.data?.items.map((c) => (
            <tr key={c.id}>
              <td><strong>{c.code}</strong><br /><small>{c.name} · {when(c.created_at)}</small></td>
              <td><StatusChip status={c.status} />{c.error && <div className="error">{c.error}</div>}</td>
              <td>{c.duration_s ? `${c.duration_s.toFixed(2)} s` : ""}</td>
              <td>{c.dialog_text}</td>
              <td className="grow"><CueStrip clip={c} /></td>
              <td><small>{c.analyser}{c.recognizer === "phonetic" ? " (built-in fallback)" : ""}</small></td>
            </tr>
          ))}
        </tbody>
      </table>
    </main>
  );
}
