import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { AccessoryDetail, api } from "../api/client";
import { AccessorySpecForm } from "../components/AccessorySpecForm";
import { ErrorNote, Field } from "../components/bits";

export function NewAccessoryPage() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [spec, setSpec] = useState<Record<string, unknown>>({ item: { id: "ACCD-GLASSES", version: "0.1" } });
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    try {
      const a = await api<AccessoryDetail>("/accessories", { method: "POST", json: {
        name, description, tags: tags.split(/[\s,]+/).filter(Boolean).map((t) => t.toLowerCase()), spec: { ...spec, name, description } } });
      navigate(`/accessories/${a.id}`);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <main>
      <h1>New accessory</h1>
      <form className="new-character" onSubmit={create}>
        <Field label="Name"><input required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description"><textarea maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label="Tags"><input placeholder="school, winter" value={tags} onChange={(e) => setTags(e.target.value)} /></Field>
        <AccessorySpecForm spec={spec} onChange={setSpec} />
        <ErrorNote error={error} />
        <button className="primary" disabled={busy || !name}>Create draft</button>
      </form>
    </main>
  );
}
