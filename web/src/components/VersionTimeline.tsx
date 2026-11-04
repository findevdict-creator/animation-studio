import { StatusChip, when } from "./bits";

/** What the timeline needs from a character or outfit version. */
export interface TimelineVersion {
  id: string; version_label?: string | null; status: string; review_decision?: string | null;
  based_on_version_id?: string | null; published_at?: string | null; created_at?: string | null;
}

/** Version history, newest first, with the "based on" link of each draft. */
export function VersionTimeline({ versions, selected, recommended, onSelect }: {
  versions: TimelineVersion[]; selected?: string; recommended?: string | null; onSelect: (id: string) => void;
}) {
  const label = (id?: string | null) => {
    const v = versions.find((x) => x.id === id);
    return v ? v.version_label ?? `draft ${v.id.slice(-6)}` : id ? id.slice(-6) : "";
  };
  return (
    <ol className="timeline" aria-label="version history">
      {versions.map((v) => (
        <li key={v.id} className={v.id === selected ? "selected" : ""}>
          <button onClick={() => onSelect(v.id)}>
            <span className="tl-label">{v.version_label ?? "draft"}</span>
            <StatusChip status={v.status} />
            {v.id === recommended && <span className="chip chip-recommended">recommended</span>}
            {v.review_decision === "reject" && v.status === "draft" && <span className="chip chip-rejected">changes requested</span>}
            <small>
              {v.based_on_version_id ? `from ${label(v.based_on_version_id)} · ` : ""}
              {when(v.published_at ?? v.created_at)}
            </small>
          </button>
        </li>
      ))}
    </ol>
  );
}
