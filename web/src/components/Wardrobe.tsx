// The outfits checked on one character version (the compatibility registry seen from the
// character). Picking one shows that fit's try-on preview in the character's viewer.
import { Link } from "react-router-dom";
import type { FitOut } from "../api/client";
import { useCan, useWardrobe } from "../api/hooks";
import { StatusChip } from "./bits";

export function Wardrobe({ characterId, code, versionId, published, selected, onSelect }: {
  characterId: string; code: string; versionId: string; published: boolean; selected?: string;
  onSelect: (fit: FitOut | undefined) => void;
}) {
  const wardrobe = useWardrobe(characterId, versionId);
  const canCreate = useCan("asset.create");
  const fits = (wardrobe.data ?? []).filter((f) => f.status !== "revoked");
  return (
    <section className="panel" aria-label="wardrobe">
      <header className="panel-head">
        <h2>Wardrobe</h2>
        {canCreate && published && <Link className="button" to={`/outfits/new?owner=${encodeURIComponent(code)}`}>New outfit for this character</Link>}
      </header>
      {!published && <p className="readonly">Outfits are fitted to published character versions.</p>}
      {published && fits.length === 0 && <p className="readonly">No outfit has been fit-checked on this version yet.</p>}
      {fits.length > 0 && (
        <ul className="wardrobe">
          {fits.map((f) => (
            <li key={f.id} className={f.id === selected ? "selected" : ""}>
              <button onClick={() => onSelect(f.id === selected ? undefined : f)} disabled={!f.files.some((x) => x.name === "preview.glb")}>
                <strong>{f.outfit.name}</strong>
                <small>{f.outfit.code} {f.outfit.version ? `v${f.outfit.version}` : "draft"}</small>
                <StatusChip status={f.status} />
              </button>
              <Link to={`/outfits/${f.outfit.id}?version=${f.outfit_version_id}`}>open</Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
