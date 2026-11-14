export type Status = "active" | "proposed" | "superseded" | "rejected";

export type Memory = {
  id: number;
  text: string;
  type: string;
  scope: string;
  status: Status;
  tags: string;
  confidence: number;
  source: string;
  created_at: string;
  use_count: number;
  last_used_at: string | null;
  superseded_by: number | null;
  meta: Record<string, unknown>;
};

export type Summary = {
  counts: Record<Status, number>;
  scopes: { scope: string; count: number }[];
  types: string[];
  needs_approval: string[];
  global: string;
};

export type Proposed = Memory & { source_label: string; related: Memory[] };
export type Detail = { item: Memory & { source_label: string }; superseded_by: Memory | null; supersedes: Memory[]; related: Memory[] };
export type Listing = { items: Memory[]; total: number; ranked: boolean };
