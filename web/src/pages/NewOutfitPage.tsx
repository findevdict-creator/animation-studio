import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, OutfitDetail } from "../api/client";
import { ErrorNote, Field } from "../components/bits";
import { OutfitSpecForm } from "../components/OutfitSpecForm";

const piece = (slot: string, id: string) => ({ slot, garment: { id, version: "0.1" } });

export function NewOutfitPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const owner = params.get("owner"); // a character code: "make an outfit for this character"
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [spec, setSpec] = useState<Record<string, unknown>>({
    fit_profiles: [params.get("fit_profile") ?? "child_average"],
    ...(owner ? { scope: "character", owner_character: owner } : { scope: "body_profile" }),
    pieces: [piece("upper", "GARM-TSHIRT"), piece("lower", "GARM-SHORTS"), piece("footwear", "GARM-SNEAKERS")],
  });
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const o = await api<OutfitDetail>("/outfits", { method: "POST", json: {
        name, description, tags: tags.split(/[\s,]+/).filter(Boolean).map((t) => t.toLowerCase()), spec: { ...spec, name, description } } });
      navigate(`/outfits/${o.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main>
      <h1>New outfit</h1>
      <form className="new-character" onSubmit={create}>
        <Field label="Name"><input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description"><textarea maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Tags"><input placeholder="school, summer" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        <OutfitSpecForm spec={spec} onChange={setSpec} />
        <ErrorNote error={error} />
        <button className="primary" disabled={busy || !name}>Create draft</button>
      </form>
    </main>
  );
}
