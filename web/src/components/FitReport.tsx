// The fit check of one outfit on one wearer: a garment × pose grid (clipping, stretch, lift)
// and the structural checks. The thresholds come with the report.
type PoseEntry = {
  tested?: number; clipped?: number; clip_share?: number; clip_depth_m?: number;
  stretch_lo?: number; stretch_hi?: number; lift_m?: number;
};
export type Report = {
  ok?: boolean; failed?: string[]; checks?: { name: string; ok: boolean; poses?: string[] }[];
  poses?: Record<string, Record<string, PoseEntry>>; pose_suite?: string[];
  wearer?: { kind?: string; name?: string; fit_profile?: string; height_m?: number };
  thresholds?: Record<string, number>;
};

/** (garment, pose) pairs a check failed on: each garment check lists its bad poses. */
export function badCells(report: Report): Set<string> {
  const bad = new Set<string>();
  for (const c of report.checks ?? []) {
    const [, garment] = c.name.split(":");
    if (!c.ok && garment) for (const p of c.poses ?? []) bad.add(`${garment}/${p}`);
  }
  return bad;
}

export function FitReport({ report }: { report: unknown }) {
  const r = (report ?? {}) as Report;
  if (!r.checks) return null;
  const failed = new Set(r.failed ?? []);
  const bad = badCells(r);
  const poses = r.pose_suite ?? Object.keys(Object.values(r.poses ?? {})[0] ?? {});
  const wearer = r.wearer?.kind === "reference" ? `reference body (${r.wearer.fit_profile})` : r.wearer?.name ?? "";
  return (
    <details className="report fit-report" open={!r.ok}>
      <summary>
        Fit check on {wearer}: {r.ok ? "passed" : "failed"} ({r.checks.filter((c) => c.ok).length}/{r.checks.length} checks)
      </summary>
      {!r.ok && <ul className="blockers">{[...failed].map((f) => <li key={f}>{f}</li>)}</ul>}
      <div className="fit-grid-wrap">
        <table className="fit-grid">
          <thead>
            <tr><th>garment</th>{poses.map((p) => <th key={p}>{p.replace(/_/g, " ")}</th>)}</tr>
          </thead>
          <tbody>
            {Object.entries(r.poses ?? {}).map(([g, per]) => (
              <tr key={g}>
                <th>{g}</th>
                {poses.map((p) => {
                  const e = per[p];
                  if (!e) return <td key={p} />;
                  const ok = !bad.has(`${g}/${p}`);
                  const title = `clipped ${e.clipped ?? 0}/${e.tested ?? 0}, depth ${((e.clip_depth_m ?? 0) * 1000).toFixed(1)} mm, ` +
                    `stretch ${e.stretch_lo ?? "-"}–${e.stretch_hi ?? "-"}${e.lift_m !== undefined ? `, lift ${(e.lift_m * 1000).toFixed(0)} mm` : ""}`;
                  return (
                    <td key={p} className={ok ? "ok" : "bad"} title={title}>
                      {e.clipped ? `${e.clipped} clip` : "✓"}
                      <small>{e.stretch_hi !== undefined ? `×${e.stretch_hi}` : ""}</small>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul>{r.checks.map((c) => <li key={c.name} className={c.ok ? "ok" : "bad"}>{c.ok ? "✓" : "✗"} {c.name}</li>)}</ul>
    </details>
  );
}
