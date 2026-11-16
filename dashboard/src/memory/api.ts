import { api } from "@/lib/api";
import type { Memory, ScopePlan, ScopeRequest, Scopes, Status, Summary } from "./types";

export const getSummary = () => api<Summary>("/api/memory/summary");
export const setStatus = (ids: number[], status: Extract<Status, "active" | "rejected">) => api("/api/memory/status", { ids, status });
export const editMemory = (body: Partial<Pick<Memory, "text" | "type" | "scope" | "tags" | "confidence">> & { id: number }) =>
  api<{ item: Memory }>("/api/memory/edit", body);
export const supersede = (id: number, text: string) => api<{ old: number; new: number }>("/api/memory/supersede", { id, text });
export const getScopes = () => api<Scopes>("/api/memory/scopes");
export const planScope = (body: ScopeRequest) => api<ScopePlan>("/api/memory/scope/plan", body);
export const applyScope = (body: ScopeRequest) => api<ScopePlan & { id: number; moved: number }>("/api/memory/scope/apply", body);
export const undoScope = (id: number) => api<{ id: number; moved_back: number; kept: number; aliases_skipped: string[] }>("/api/memory/scope/undo", { id });
