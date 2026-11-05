import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AccessoryDef, AccessoryDetail, AccessoryOut, AccessoryVersionDetail, AnimationDetail, AnimationOut,
  AnimationVersionDetail, AudioOut, BoneMap, ClipDef, GeneratorDef, SceneDetail, SceneOut,
  SceneVersionDetail,
  api, CharacterDetail, CharacterOut, Conventions, FitDetail, FitOut, Garment, JobOut, Me, OutfitDetail, OutfitOut,
  OutfitVersionDetail, VersionDetail, VersionJobOut,
} from "./client";

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me"), staleTime: 60_000 });

export function useCan(permission: string): boolean {
  const me = useMe();
  return !!me.data?.permissions.includes(permission);
}

export interface GalleryFilter { q?: string; tag?: string; status?: string }

export const useGallery = (f: GalleryFilter) =>
  useQuery({
    queryKey: ["characters", f],
    queryFn: () => {
      const p = new URLSearchParams();
      if (f.q) p.set("q", f.q);
      if (f.tag) p.set("tag", f.tag);
      if (f.status) p.set("status", f.status);
      return api<{ items: CharacterOut[]; next_cursor: string | null }>(`/characters?${p}`);
    },
  });

export const useCharacter = (id: string) =>
  useQuery({ queryKey: ["character", id], queryFn: () => api<CharacterDetail>(`/characters/${id}`) });

export const useVersion = (cid: string, vid: string | undefined) =>
  useQuery({
    queryKey: ["version", cid, vid],
    queryFn: () => api<VersionDetail>(`/characters/${cid}/versions/${vid}`),
    enabled: !!vid,
  });

export const useJob = (id: string | undefined) =>
  useQuery({
    queryKey: ["job", id],
    queryFn: () => api<JobOut>(`/jobs/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && ["succeeded", "failed", "cancelled", "timed_out"].includes(q.state.data.status) ? false : 1500),
  });

/** Every mutation on a character refreshes its detail, version and gallery queries. */
export function useCharacterAction<TBody, TOut = VersionDetail | VersionJobOut>(cid: string, run: (body: TBody) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["character", cid] });
      qc.invalidateQueries({ queryKey: ["version", cid] });
      qc.invalidateQueries({ queryKey: ["characters"] });
    },
  });
}

// ---------------------------------------------------------------- outfits
export interface OutfitFilter { q?: string; slot?: string; scope?: string; status?: string; owner_character_id?: string }

export const useOutfits = (f: OutfitFilter) =>
  useQuery({
    queryKey: ["outfits", f],
    queryFn: () => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
      return api<{ items: OutfitOut[]; next_cursor: string | null }>(`/outfits?${p}`);
    },
  });

export const useOutfit = (id: string) =>
  useQuery({ queryKey: ["outfit", id], queryFn: () => api<OutfitDetail>(`/outfits/${id}`) });

export const useOutfitVersion = (oid: string, vid: string | undefined) =>
  useQuery({
    queryKey: ["outfit-version", oid, vid],
    queryFn: () => api<OutfitVersionDetail>(`/outfits/${oid}/versions/${vid}`),
    enabled: !!vid,
  });

export const useFits = (oid: string, vid: string | undefined) =>
  useQuery({
    queryKey: ["fits", oid, vid],
    queryFn: () => api<FitOut[]>(`/outfits/${oid}/versions/${vid}/fits`),
    enabled: !!vid,
  });

export const useFit = (oid: string, vid: string | undefined, cvid: string | undefined) =>
  useQuery({
    queryKey: ["fit", oid, vid, cvid],
    queryFn: () => api<FitDetail>(`/outfits/${oid}/versions/${vid}/fits/${cvid}`),
    enabled: !!vid && !!cvid,
  });

export const useWardrobe = (cid: string, cvid: string | undefined) =>
  useQuery({
    queryKey: ["wardrobe", cid, cvid],
    queryFn: () => api<FitOut[]>(`/characters/${cid}/versions/${cvid}/outfits`),
    enabled: !!cvid,
  });

export const useGarments = () =>
  useQuery({ queryKey: ["garments"], queryFn: () => api<Garment[]>("/garments"), staleTime: Infinity });

export const useClothing = (ref = "family_cartoon@1.0") =>
  useQuery({ queryKey: ["clothing", ref], queryFn: () => api<Conventions>(`/style-profiles/${ref}/clothing`), staleTime: Infinity });

/** Every mutation on an outfit refreshes its detail, versions, fits and the gallery. */
export function useOutfitAction<TBody, TOut = unknown>(oid: string, run: (body: TBody) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      for (const key of [["outfit", oid], ["outfit-version", oid], ["outfits"], ["fits", oid], ["fit", oid], ["wardrobe"]])
        qc.invalidateQueries({ queryKey: key });
    },
  });
}

// ---------------------------------------------------------------- accessories
export interface AccessoryFilter { q?: string; category?: string; status?: string; attach_point?: string; fit_profile?: string }

export const useAccessories = (f: AccessoryFilter) =>
  useQuery({
    queryKey: ["accessories", f],
    queryFn: () => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
      return api<{ items: AccessoryOut[]; next_cursor: string | null }>(`/accessories?${p}`);
    },
  });

export const useAccessory = (id: string) =>
  useQuery({ queryKey: ["accessory", id], queryFn: () => api<AccessoryDetail>(`/accessories/${id}`) });

export const useAccessoryVersion = (aid: string, vid: string | undefined) =>
  useQuery({
    queryKey: ["accessory-version", aid, vid],
    queryFn: () => api<AccessoryVersionDetail>(`/accessories/${aid}/versions/${vid}`),
    enabled: !!vid,
  });

export const useAccessoryItems = () =>
  useQuery({ queryKey: ["accessory-items"], queryFn: () => api<AccessoryDef[]>("/accessory-items"), staleTime: Infinity });

/** Every mutation on an accessory refreshes its detail, versions and the gallery. */
export function useAccessoryAction<TBody, TOut = unknown>(aid: string, run: (body: TBody) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      for (const key of [["accessory", aid], ["accessory-version", aid], ["accessories"]]) qc.invalidateQueries({ queryKey: key });
    },
  });
}

// ---------------------------------------------------------------- animations
export interface AnimationFilter { q?: string; category?: string; status?: string; source_kind?: string; fit_profile?: string }

export const useAnimations = (f: AnimationFilter) =>
  useQuery({
    queryKey: ["animations", f],
    queryFn: () => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
      return api<{ items: AnimationOut[]; next_cursor: string | null }>(`/animations?${p}`);
    },
  });

export const useAnimation = (id: string) =>
  useQuery({ queryKey: ["animation", id], queryFn: () => api<AnimationDetail>(`/animations/${id}`) });

export const useAnimationVersion = (aid: string, vid: string | undefined) =>
  useQuery({
    queryKey: ["animation-version", aid, vid],
    queryFn: () => api<AnimationVersionDetail>(`/animations/${aid}/versions/${vid}`),
    enabled: !!vid,
  });

export const useGenerators = () =>
  useQuery({ queryKey: ["animation-generators"], queryFn: () => api<GeneratorDef[]>("/animation-generators"), staleTime: Infinity });

export const useBoneMaps = () =>
  useQuery({ queryKey: ["bone-maps"], queryFn: () => api<BoneMap[]>("/bone-maps"), staleTime: Infinity });

/** Every mutation on an animation refreshes its detail, versions and the gallery. */
export function useAnimationAction<TBody, TOut = unknown>(aid: string, run: (body: TBody) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      for (const key of [["animation", aid], ["animation-version", aid], ["animations"]]) qc.invalidateQueries({ queryKey: key });
    },
  });
}

// ---------------------------------------------------------------- dialogue audio
export const useAudioClips = (f: { status?: string; q?: string } = {}) =>
  useQuery({
    queryKey: ["audio-clips", f],
    queryFn: () => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
      return api<{ items: AudioOut[]; next_cursor: string | null }>(`/audio-clips?${p}`);
    },
    // poll while a clip is being analysed
    refetchInterval: (q) => (q.state.data?.items.some((c) => c.status === "analysing") ? 2000 : false),
  });

// ---------------------------------------------------------------- scenes
export const useScenes = (f: { q?: string; uses_version_id?: string }) =>
  useQuery({
    queryKey: ["scenes", f],
    queryFn: () => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
      return api<{ items: SceneOut[]; next_cursor: string | null }>(`/scenes?${p}`);
    },
  });

export const useScene = (id: string | undefined) =>
  useQuery({ queryKey: ["scene", id], queryFn: () => api<SceneDetail>(`/scenes/${id}`), enabled: !!id });

export const useSceneVersion = (sid: string | undefined, vid: string | undefined) =>
  useQuery({
    queryKey: ["scene-version", sid, vid],
    queryFn: () => api<SceneVersionDetail>(`/scenes/${sid}/versions/${vid}`),
    enabled: !!sid && !!vid,
    // poll while a build or a reopen check is queued
    refetchInterval: (q) => (q.state.data && (q.state.data.build_status === "queued" || q.state.data.verify_status === "queued") ? 2000 : false),
  });

export const useClips = () =>
  useQuery({ queryKey: ["clips"], queryFn: () => api<ClipDef[]>("/animation-clips"), staleTime: Infinity });

export function useSceneAction<TBody, TOut = unknown>(sid: string, run: (body: TBody) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      for (const key of [["scene", sid], ["scene-version", sid], ["scenes"]]) qc.invalidateQueries({ queryKey: key });
    },
  });
}
