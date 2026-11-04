import { type ReactNode, useEffect } from "react";
import { ApiProblem } from "../api/client";
import { useJob } from "../api/hooks";

export function StatusChip({ status }: { status: string }) {
  return <span className={`chip chip-${status}`}>{status}</span>;
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as Error;
  const problems = e instanceof ApiProblem ? (e.body.problems as string[] | undefined) ?? (e.body.issues as { path: string; message: string }[] | undefined)?.map((i) => `${i.path}: ${i.message}`) : undefined;
  return (
    <div className="error" role="alert">
      {e instanceof ApiProblem ? <strong>{e.code}</strong> : null} {e.message}
      {problems && <ul>{problems.map((p) => <li key={p}>{p}</li>)}</ul>}
    </div>
  );
}

export function JobProgress({ id, onDone }: { id: string; onDone?: () => void }) {
  const job = useJob(id);
  const j = job.data;
  const done = !!j && ["succeeded", "failed", "cancelled", "timed_out"].includes(j.status);
  useEffect(() => { if (done) onDone?.(); }, [done]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!j) return <div className="job">job {id.slice(0, 8)} …</div>;
  return (
    <div className={`job job-${j.status}`}>
      <div className="job-head">
        <span>{j.type}</span> <StatusChip status={j.status} />
      </div>
      {!done && <progress value={j.progress} max={1} />}
      <small>{j.progress_message}</small>
      {j.error && <pre className="job-error">{String((j.error as { message?: string }).message ?? JSON.stringify(j.error))}</pre>}
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

export function bytes(n: number): string {
  return n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : n > 1e3 ? `${(n / 1e3).toFixed(0)} kB` : `${n} B`;
}

export function when(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "";
}
