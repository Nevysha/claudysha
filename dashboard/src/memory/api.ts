import { api } from "@/lib/api";
import type { Memory, Status, Summary } from "./types";

export const getSummary = () => api<Summary>("/api/memory/summary");
export const setStatus = (ids: number[], status: Extract<Status, "active" | "rejected">) => api("/api/memory/status", { ids, status });
export const editMemory = (body: Partial<Pick<Memory, "text" | "type" | "scope" | "tags" | "confidence">> & { id: number }) =>
  api<{ item: Memory }>("/api/memory/edit", body);
export const supersede = (id: number, text: string) => api<{ old: number; new: number }>("/api/memory/supersede", { id, text });
