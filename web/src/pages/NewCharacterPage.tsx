import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, CharacterDetail } from "../api/client";
import { ErrorNote, Field } from "../components/bits";
import { SpecForm } from "../components/SpecForm";

const START = { body: { age_appearance: "child", body_type: "average", height_m: 1.25 }, hair: { style: "short", color: "dark_brown" } };

export function NewCharacterPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [spec, setSpec] = useState<Record<string, unknown>>(START);
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const c = await api<CharacterDetail>("/characters", { method: "POST", json: {
        name, description, tags: tags.split(/[\s,]+/).filter(Boolean).map((t) => t.toLowerCase()), spec: { ...spec, name, description } } });
      navigate(`/characters/${c.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main>
      <h1>New character</h1>
      <form className="new-character" onSubmit={create}>
        <Field label="Name"><input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description"><textarea maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Tags"><input placeholder="child, family" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        <SpecForm spec={spec} onChange={setSpec} />
        <ErrorNote error={error} />
        <button className="primary" disabled={busy || !name}>Create draft</button>
      </form>
    </main>
  );
}
