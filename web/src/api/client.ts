// A small typed wrapper over fetch for the v1 API. Types come from the OpenAPI document
// (npm run api-types regenerates src/api/schema.d.ts from a running backend).
import { accessToken } from "../auth";
import type { components } from "./schema";

export type S = components["schemas"];
export type CharacterOut = S["CharacterOut"];
export type CharacterDetail = S["CharacterDetail"];
export type VersionSummary = S["VersionSummary"];
export type VersionDetail = S["VersionDetail"];
export type FileOut = S["FileOut"];
export type JobOut = S["JobOut"];
export type VersionJobOut = S["VersionJobOut"];
export type Me = S["MeOut"];
export type OutfitOut = S["OutfitOut"];
export type OutfitDetail = S["OutfitDetail"];
export type OutfitVersionSummary = S["OutfitVersionSummary"];
export type OutfitVersionDetail = S["OutfitVersionDetail"];
export type OutfitVersionJobOut = S["OutfitVersionJobOut"];
export type FitOut = S["FitOut"];
export type FitDetail = S["FitDetail"];
export type FitJobOut = S["FitJobOut"];
export type Garment = S["GarmentDef"];
export type Conventions = S["ConventionsOut"];

export const API = "/api/v1";

export class ApiProblem extends Error {
  constructor(public status: number, public code: string, detail: string, public body: Record<string, unknown>) {
    super(detail || code);
  }
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown; idempotencyKey?: string } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${await accessToken()}`);
  if (init.json !== undefined) headers.set("Content-Type", "application/json");
  if (init.idempotencyKey) headers.set("Idempotency-Key", init.idempotencyKey);
  const res = await fetch(API + path, { ...init, headers, body: init.json !== undefined ? JSON.stringify(init.json) : init.body });
  if (res.status === 204) return undefined as T;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiProblem(res.status, body.code ?? "error", body.detail ?? res.statusText, body);
  return body as T;
}

export async function fileUrl(characterId: string, versionId: string, name: string): Promise<string> {
  const r = await api<{ url: string }>(`/characters/${characterId}/versions/${versionId}/files/${name}?redirect=false`);
  return r.url;
}

export async function outfitFileUrl(outfitId: string, versionId: string, name: string): Promise<string> {
  const r = await api<{ url: string }>(`/outfits/${outfitId}/versions/${versionId}/files/${name}?redirect=false`);
  return r.url;
}
