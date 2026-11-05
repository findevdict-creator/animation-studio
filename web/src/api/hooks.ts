import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AccessoryDef, AccessoryDetail, AccessoryOut, AccessoryVersionDetail, AnimationDetail, AnimationOut,
  AnimationVersionDetail, AudioOut, BoneMap, ClipDef, GeneratorDef, SceneDetail, SceneOut,
  SceneVersionDetail,
  api, CharacterDetail, CharacterOut, Conventions, FitDetail, FitOut, Garment, JobOut, Me, OutfitDetail, OutfitOut,
  OutfitVersionDetail, VersionDetail, VersionJobOut,
  EnvironmentDef, LightingPreset, PropDef, SetDetail, SetOut, SetPlural, SetVersionDetail,
  EpisodeOut, EpisodeVersionOut, RenderLog, RenderOut, SeriesOut, StoryboardOut,
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

// ---------------------------------------------------------------- props and environments
export interface SetFilter { q?: string; tag?: string; category?: string; status?: string }

export const useSets = <T extends SetOut = SetOut>(plural: SetPlural, f: SetFilter) =>
  useQuery({
    queryKey: [plural, f],
    queryFn: () => {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(f)) if (v) p.set(k, v);
      return api<{ items: T[]; next_cursor: string | null }>(`/${plural}?${p}`);
    },
  });

export const useSet = <T extends SetDetail = SetDetail>(plural: SetPlural, id: string) =>
  useQuery({ queryKey: [plural, "one", id], queryFn: () => api<T>(`/${plural}/${id}`), enabled: !!id });

export const useSetVersion = <T extends SetVersionDetail = SetVersionDetail>(plural: SetPlural, id: string, vid: string | undefined) =>
  useQuery({
    queryKey: [plural, "version", id, vid],
    queryFn: () => api<T>(`/${plural}/${id}/versions/${vid}`),
    enabled: !!id && !!vid,
  });

export const usePropItems = () =>
  useQuery({ queryKey: ["prop-items"], queryFn: () => api<PropDef[]>("/prop-items"), staleTime: Infinity });
export const useEnvironmentTemplates = () =>
  useQuery({ queryKey: ["environment-templates"], queryFn: () => api<EnvironmentDef[]>("/environment-templates"), staleTime: Infinity });
export const useLightingPresets = () =>
  useQuery({ queryKey: ["lighting-presets"], queryFn: () => api<LightingPreset[]>("/lighting-presets"), staleTime: Infinity });

/** Every mutation on a prop or environment refreshes everything under its collection key. */
export function useSetAction<TBody, TOut = unknown>(plural: SetPlural, run: (body: TBody) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: run, onSuccess: () => qc.invalidateQueries({ queryKey: [plural] }) });
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
export const useAudioClips = (f: { status?: string; q?: string; kind?: string } = {}) =>
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

// ---------------------------------------------------------------- series, episodes, renders
export const useSeriesList = () =>
  useQuery({ queryKey: ["series"], queryFn: () => api<{ items: SeriesOut[] }>("/series") });

export const useSeries = (ref: string | undefined) =>
  useQuery({ queryKey: ["series", ref], queryFn: () => api<SeriesOut>(`/series/${ref}`), enabled: !!ref });

export const useEpisodes = (series?: string) =>
  useQuery({
    queryKey: ["episodes", series],
    queryFn: () => api<{ items: EpisodeOut[] }>(`/episodes${series ? `?series=${encodeURIComponent(series)}` : ""}`),
  });

export const useEpisode = (ref: string | undefined) =>
  useQuery({ queryKey: ["episode", ref], queryFn: () => api<EpisodeOut>(`/episodes/${ref}`), enabled: !!ref });

export const useStoryboard = (id: string | undefined) =>
  useQuery({ queryKey: ["storyboard", id], queryFn: () => api<StoryboardOut>(`/storyboards/${id}`), enabled: !!id });

const busyShot = (v: EpisodeVersionOut) => v.shots.some((s) => s.build_status === "queued" || s.verify_status === "queued");

export const useEpisodeVersion = (id: string | undefined) =>
  useQuery({
    queryKey: ["episode-version", id],
    queryFn: () => api<EpisodeVersionOut>(`/episode-versions/${id}`),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && busyShot(q.state.data) ? 2500 : false),
  });

export const RENDER_DONE = ["completed", "failed", "cancelled"];

export const useRenders = (episode: string | undefined) =>
  useQuery({
    queryKey: ["renders", episode],
    queryFn: () => api<{ items: RenderOut[] }>(`/render-jobs?episode=${encodeURIComponent(episode ?? "")}`),
    enabled: !!episode,
    refetchInterval: (q) => (q.state.data?.items.some((r) => !RENDER_DONE.includes(r.status)) ? 3000 : false),
  });

export const useRender = (ref: string | undefined) =>
  useQuery({
    queryKey: ["render", ref],
    queryFn: () => api<RenderOut>(`/render-jobs/${ref}`),
    enabled: !!ref,
    refetchInterval: (q) => (q.state.data && !RENDER_DONE.includes(q.state.data.status) ? 2000 : false),
  });

export const useRenderLogs = (ref: string | undefined, open: boolean) =>
  useQuery({ queryKey: ["render-logs", ref], queryFn: () => api<RenderLog[]>(`/render-jobs/${ref}/logs`), enabled: !!ref && open });

/** Every mutation in the episode editor refreshes the episode and everything under it. */
export function useEpisodeAction<TBody, TOut = unknown>(run: (body: TBody) => Promise<TOut>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: run,
    onSuccess: () => {
      for (const key of [["episode"], ["episodes"], ["storyboard"], ["episode-version"], ["renders"], ["render"], ["series"],
        ["audio-clips"]])
        qc.invalidateQueries({ queryKey: key });
    },
  });
}
