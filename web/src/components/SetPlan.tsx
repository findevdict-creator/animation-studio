// A top-down plan of an environment version's layout (environment_versions.layout): the floor,
// solid dressing, tops and seats, marks with their facing, and camera marks. A scene editor
// overlays its own props and character instances. Metres; +Y is north (up on the plan) and a
// character at 0° faces south (-Y), towards the default cameras.

export type Footprint = [string, number, number, number, number, number, ...number[]];
export type LayoutMark = { name: string; kind: string; at: [number, number]; facing_deg: number; item?: string | null };
export type LayoutCamera = { name: string; at: [number, number, number]; look_at: [number, number, number]; lens_mm: number };
export type Layout = {
  kind: "room" | "outdoor"; size: [number, number, number]; lighting: string;
  solids: Footprint[]; tops: Footprint[]; seats: Footprint[]; marks: LayoutMark[]; cameras: LayoutCamera[];
};
export type PlanDot = { key: string; at: [number, number]; facing_deg?: number; kind: "character" | "prop"; size?: [number, number] };

/** The direction a character turned ``deg`` about +Z faces, from facing -Y at 0°. */
export function facing(deg: number): [number, number] {
  const r = (deg * Math.PI) / 180;
  return [Math.sin(r), -Math.cos(r)];
}

export function asLayout(x: unknown): Layout | undefined {
  const l = x as Layout | null | undefined;
  return l && Array.isArray(l.size) && Array.isArray(l.solids) ? l : undefined;
}

function Box({ f, className }: { f: Footprint; className: string }) {
  const [id, cx, cy, w, d, rot] = f;
  return (
    <g transform={`translate(${cx} ${-cy}) rotate(${-rot})`}>
      <rect className={className} x={-w / 2} y={-d / 2} width={w} height={d}><title>{id}</title></rect>
    </g>
  );
}

function Arrow({ at, deg, len, className }: { at: [number, number]; deg: number; len: number; className: string }) {
  const [dx, dy] = facing(deg);
  return <line className={className} x1={at[0]} y1={-at[1]} x2={at[0] + dx * len} y2={-(at[1] + dy * len)} />;
}

export function SetPlan({ layout, dots = [], label = "plan", highlight }: {
  layout: Layout; dots?: PlanDot[]; label?: string; highlight?: string;
}) {
  const [w, d] = layout.size;
  const pad = Math.max(w, d) * 0.08 + 0.3;
  const cams = layout.cameras.map((c) => [c.at[0], c.at[1]] as [number, number]);
  const xs = [-w / 2, w / 2, ...cams.map((c) => c[0])];
  const ys = [-d / 2, d / 2, ...cams.map((c) => c[1])];
  const minX = Math.min(...xs) - pad, maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad, maxY = Math.max(...ys) + pad;
  const unit = Math.max(w, d) / 40;
  return (
    <svg className="set-plan" role="img" aria-label={label} viewBox={`${minX} ${-maxY} ${maxX - minX} ${maxY - minY}`}
      style={{ ["--u" as string]: unit }}>
      <rect className={`floor floor-${layout.kind}`} x={-w / 2} y={-d / 2} width={w} height={d} />
      {layout.kind === "room" && <line className="cutaway" x1={-w / 2} y1={d / 2} x2={w / 2} y2={d / 2} />}
      {layout.solids.map((f) => <Box key={`s${f[0]}`} f={f} className="solid" />)}
      {layout.tops.map((f) => <Box key={`t${f[0]}`} f={f} className="top" />)}
      {layout.seats.map((f) => <Box key={`e${f[0]}`} f={f} className="seat" />)}
      {layout.cameras.map((c) => (
        <g key={c.name} className={`camera${highlight === `camera:${c.name}` ? " on" : ""}`}>
          <line x1={c.at[0]} y1={-c.at[1]} x2={c.look_at[0]} y2={-c.look_at[1]} />
          <circle cx={c.at[0]} cy={-c.at[1]} r={unit * 0.9}><title>camera {c.name} ({c.lens_mm} mm)</title></circle>
        </g>
      ))}
      {layout.marks.map((m) => (
        <g key={m.name} className={`mark mark-${m.kind}${highlight === `mark:${m.name}` ? " on" : ""}`}>
          <circle cx={m.at[0]} cy={-m.at[1]} r={unit * 1.2}><title>{m.kind} mark {m.name}</title></circle>
          <Arrow at={m.at} deg={m.facing_deg} len={unit * 2.4} className="facing" />
        </g>
      ))}
      {dots.map((p) => (
        <g key={`${p.kind}:${p.key}`} className={`dot dot-${p.kind}`}>
          {p.size
            ? <rect x={p.at[0] - p.size[0] / 2} y={-p.at[1] - p.size[1] / 2} width={p.size[0]} height={p.size[1]}><title>{p.key}</title></rect>
            : <circle cx={p.at[0]} cy={-p.at[1]} r={p.kind === "character" ? 0.22 : unit}><title>{p.key}</title></circle>}
          {p.facing_deg !== undefined && <Arrow at={p.at} deg={p.facing_deg} len={0.4} className="facing" />}
          <text x={p.at[0]} y={-p.at[1] - unit * 1.6}>{p.key}</text>
        </g>
      ))}
    </svg>
  );
}
