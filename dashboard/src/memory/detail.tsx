import { Button, Card, CardContent, Label, Textarea, toast } from "@nevysha/ui";
import { ArrowRight, Check, Pencil, Replace, Undo2, X } from "lucide-react";
import { useId, useState } from "react";
import { api, query } from "@/lib/api";
import { datetime } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { useNav } from "@/shell/nav";
import { KeyValue, SectionTitle } from "@/ui/page";
import { setStatus, supersede } from "./api";
import { EditForm, ScopeBadge, StatusBadge, TypeBadge } from "./bits";
import type { Detail, Memory, Summary } from "./types";

type Props = { id: number; summary: Summary; onOpen: (id: number) => void; onClose: () => void; onChanged: (id: number) => void };

export function MemoryDetail({ id, summary, onOpen, onClose, onChanged }: Props) {
  const nav = useNav();
  const { data: d, error } = useLoad(() => api<Detail>(`/api/memory/item${query({ id })}`), [id]);
  const [mode, setMode] = useState<"view" | "edit" | "supersede">("view");
  const { busy, run } = useAction();
  const textId = useId();
  const [newText, setNewText] = useState<string | null>(null);

  if (error) return <Card><CardContent className="text-danger-text">{error}</CardContent></Card>;
  if (!d) return <Card><CardContent className="text-text-muted">Loading…</CardContent></Card>;
  const m = d.item;

  const after = (title: string, next: number) => {
    toast({ title, variant: "success" });
    nav.refresh();
    onChanged(next);
  };
  const status = (s: "active" | "rejected", title: string) => run(async () => { await setStatus([m.id], s); after(title, m.id); });
  const link = (x: Memory, prefix: string) => (
    <button key={x.id} type="button" className="text-left hover:text-accent-text focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none" onClick={() => onOpen(x.id)}>
      <span className="font-mono text-text-muted">{prefix}#{x.id} </span>
      {x.text}
    </button>
  );

  return (
    <Card className="lg:sticky lg:top-4">
      <CardContent className="grid gap-4">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono font-semibold">#{m.id}</span>
          <TypeBadge type={m.type} />
          <StatusBadge status={m.status} />
          <Button size="icon" variant="ghost" className="ml-auto" aria-label="Close" onClick={onClose}><X aria-hidden /></Button>
        </div>

        {mode === "edit" ? (
          <EditForm m={m} summary={summary} full onCancel={() => setMode("view")} onSaved={(u) => after(`#${u.id} saved`, u.id)} />
        ) : mode === "supersede" ? (
          <div className="grid gap-2">
            <Label htmlFor={textId}>Up-to-date version</Label>
            <p className="text-sm text-text-muted">The old one is kept, marked as replaced, so its history stays.</p>
            <Textarea id={textId} rows={5} value={newText ?? m.text} onChange={(e) => setNewText(e.target.value)} />
            <div className="flex gap-2">
              <Button size="sm" disabled={busy} onClick={() => run(async () => { const r = await supersede(m.id, newText ?? m.text); after(`#${r.old} replaced by #${r.new}`, r.new); })}>Replace</Button>
              <Button size="sm" variant="ghost" onClick={() => setMode("view")}>Cancel</Button>
            </div>
          </div>
        ) : (
          <>
            <p className="text-base whitespace-pre-wrap">{m.text}</p>
            <div className="flex flex-wrap gap-2">
              {m.status === "proposed" && <Button size="sm" disabled={busy} onClick={() => status("active", `#${m.id} approved`)}><Check aria-hidden /> Approve</Button>}
              {m.status === "rejected" && <Button size="sm" disabled={busy} onClick={() => status("active", `#${m.id} restored`)}><Undo2 aria-hidden /> Restore</Button>}
              {m.status !== "superseded" && <Button size="sm" variant="secondary" onClick={() => setMode("edit")}><Pencil aria-hidden /> Edit</Button>}
              {(m.status === "active" || m.status === "proposed") && (
                <>
                  <Button size="sm" variant="secondary" onClick={() => setMode("supersede")}><Replace aria-hidden /> Replace</Button>
                  <Button size="sm" variant="danger" disabled={busy} onClick={() => status("rejected", `#${m.id} rejected`)}><X aria-hidden /> {m.status === "active" ? "Forget" : "Reject"}</Button>
                </>
              )}
            </div>
            <KeyValue
              className="text-sm"
              items={[
                ["Scope", <ScopeBadge scope={m.scope} />],
                ["Tags", m.tags || <span className="text-text-muted">none</span>],
                ["Confidence", m.confidence.toFixed(2)],
                ["From", m.source_label],
                ["Created", datetime(m.created_at)],
                ["Used", `${m.use_count}× by sessions${m.last_used_at ? `, last ${datetime(m.last_used_at)}` : ""}`],
              ]}
            />
            {d.superseded_by && (
              <div className="grid gap-2">
                <SectionTitle className="text-base">Replaced by</SectionTitle>
                <div className="flex items-start gap-1 text-sm"><ArrowRight className="mt-0.5 size-4 shrink-0" aria-hidden />{link(d.superseded_by, "")}</div>
              </div>
            )}
            {d.supersedes.length > 0 && (
              <div className="grid gap-2 text-sm">
                <SectionTitle className="text-base">Replaces</SectionTitle>
                {d.supersedes.map((x) => link(x, "← "))}
              </div>
            )}
            {d.related.length > 0 && (
              <div className="grid gap-2 text-sm">
                <SectionTitle className="text-base">Related</SectionTitle>
                {d.related.map((x) => link(x, ""))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
