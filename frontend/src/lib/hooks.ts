import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export const useClients = () => useQuery({ queryKey: ["clients"], queryFn: () => api.get<any[]>("/clients") });
export const useBusinesses = () => useQuery({ queryKey: ["businesses"], queryFn: () => api.get<any[]>("/businesses") });

/** Mutation that invalidates the given query keys on success. */
export function useAction<T = any>(fn: (v: any) => Promise<T>, invalidate: string[][] = []) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => invalidate.forEach((k) => qc.invalidateQueries({ queryKey: k })),
  });
}

export function clientOptions(clients?: any[], withAll = false) {
  const opts = (clients ?? []).map((c) => ({ value: c.id, label: c.name }));
  return withAll ? [{ value: "", label: "All clients" }, ...opts] : opts;
}
