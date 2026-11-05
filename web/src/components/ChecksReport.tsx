// A pass/fail list of named checks: the accessory attach check, a scene's accessory checks and
// the reopen check of a saved scene. Each check's extra fields are shown on hover.
type Check = { name: string; ok: boolean; [k: string]: unknown };
export type ChecksDoc = { ok?: boolean; failed?: string[]; checks?: Check[] };

export function detailOf(c: Check): string {
  const rest = Object.fromEntries(Object.entries(c).filter(([k]) => k !== "name" && k !== "ok"));
  return Object.keys(rest).length ? JSON.stringify(rest) : "";
}

export function ChecksReport({ title, report }: { title: string; report: unknown }) {
  const r = (report ?? {}) as ChecksDoc;
  if (!r.checks) return null;
  const passed = r.checks.filter((c) => c.ok).length;
  return (
    <details className="report checks-report" open={r.ok === false}>
      <summary>{title}: {r.ok ? "passed" : "failed"} ({passed}/{r.checks.length} checks)</summary>
      <ul className="checks">
        {r.checks.map((c) => (
          <li key={c.name} className={c.ok ? "ok" : "bad"} title={detailOf(c)}>
            <span aria-label={c.ok ? "passed" : "failed"}>{c.ok ? "✓" : "✗"}</span> {c.name}
          </li>
        ))}
      </ul>
    </details>
  );
}
