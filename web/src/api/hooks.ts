import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, CharacterDetail, CharacterOut, JobOut, Me, VersionDetail, VersionJobOut } from "./client";

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
