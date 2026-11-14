import { Button, Card, CardContent, Checkbox, Label, toast } from "@nevysha/ui";
import { Check, PartyPopper, Pencil, X } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import { date } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { useNav } from "@/shell/nav";
import { Empty, PageHead, SectionTitle } from "@/ui/page";
import { getSummary, setStatus } from "./api";
import { EditForm, ScopeBadge, StatusBadge, TypeBadge } from "./bits";
import type { Proposed, Summary } from "./types";

export function Review() {
  const nav = useNav();
  const { data, error, setData } = useLoad(() => Promise.all([api<{ items: Proposed[] }>("/api/memory/proposed"), getSummary()]), []);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const bulk = useAction();
  if (error) throw new Error(error);
  if (!data) return <p className="text-text-muted">Loading…</p>;
  const [{ items }, summary] = data;

  const leave = (ids: number[]) => {
    setData([{ items: items.filter((m) => !ids.includes(m.id)) }, summary]);
    setSelected((s) => new Set([...s].filter((id) => !ids.includes(id))));
    nav.refresh();
  };
  const decide = async (ids: number[], status: "active" | "rejected") => {
    await setStatus(ids, status);
    toast({ title: `${status === "active" ? "Approved" : "Rejected"} ${ids.length === 1 ? `#${ids[0]}` : ids.length}`, variant: "success" });
    leave(ids);
  };
  const toggle = (id: number, on: boolean) =>
    setSelected((s) => {
      const next = new Set(s);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const n = selected.size;
  const all = n > 0 && n === items.length ? true : n > 0 ? "indeterminate" : false;
  const groups = new Map<string, Proposed[]>();
  items.forEach((m) => groups.set(m.scope, [...(groups.get(m.scope) ?? []), m]));

  return (
    <>
      <PageHead
        title="Review"
        actions={
          items.length > 0 && (
            <>
              <div className="flex items-center gap-2">
                <Checkbox id="select-all" checked={all} onCheckedChange={(c) => setSelected(c === true ? new Set(items.map((m) => m.id)) : new Set())} />
                <Label htmlFor="select-all">All</Label>
              </div>
              <Button disabled={!n || bulk.busy} onClick={() => bulk.run(() => decide([...selected], "active"))}>
                <Check aria-hidden /> Approve {n || ""}
              </Button>
              <Button variant="danger" disabled={!n || bulk.busy} onClick={() => bulk.run(() => decide([...selected], "rejected"))}>
                <X aria-hidden /> Reject {n || ""}
              </Button>
            </>
          )
        }
      >
        {items.length
          ? `${items.length} proposed memor${items.length === 1 ? "y" : "ies"} about how Claude should work with you. Nothing here is used until you approve it.`
          : "Proposed memories about how Claude should work with you wait here for your OK."}
      </PageHead>

      {!items.length ? (
        <Empty icon={<PartyPopper aria-hidden />}>Nothing to review.</Empty>
      ) : (
        [...groups].map(([scope, ms]) => (
          <section key={scope} className="grid gap-3">
            <SectionTitle>
              <ScopeBadge scope={scope} />
              <span className="text-sm font-normal text-text-muted">{scope === summary.global ? "applies to every session" : "applies to sessions in this scope"}</span>
            </SectionTitle>
            {ms.map((m) => (
              <ProposedCard key={m.id} m={m} summary={summary} selected={selected.has(m.id)} onSelect={(on) => toggle(m.id, on)} onDecide={decide}
                onEdited={(u) => setData([{ items: items.map((x) => (x.id === u.id ? { ...x, ...u } : x)) }, summary])} />
            ))}
          </section>
        ))
      )}
    </>
  );
}

function ProposedCard({ m, summary, selected, onSelect, onDecide, onEdited }: {
  m: Proposed; summary: Summary; selected: boolean; onSelect: (on: boolean) => void;
  onDecide: (ids: number[], status: "active" | "rejected") => Promise<void>; onEdited: (m: Proposed) => void;
}) {
  const [editing, setEditing] = useState(false);
  const { busy, run } = useAction();
  return (
    <Card className={selected ? "border-accent" : undefined}>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap items-center gap-2 text-sm text-text-muted">
          <Checkbox aria-label={`Select #${m.id}`} checked={selected} onCheckedChange={(c) => onSelect(c === true)} />
          <span className="font-mono font-semibold text-text">#{m.id}</span>
          <TypeBadge type={m.type} />
          <ScopeBadge scope={m.scope} />
          <span>{date(m.created_at)}</span>
          <span title="confidence">c{m.confidence.toFixed(1)}</span>
        </div>
        {editing ? (
          <EditForm m={m} summary={summary} onCancel={() => setEditing(false)} onSaved={(u) => { onEdited({ ...m, ...u }); setEditing(false); toast({ title: `#${m.id} saved`, variant: "success" }); }} />
        ) : (
          <>
            <p className="text-base whitespace-pre-wrap">{m.text}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" disabled={busy} onClick={() => run(() => onDecide([m.id], "active"))}><Check aria-hidden /> Approve</Button>
              <Button size="sm" variant="danger" disabled={busy} onClick={() => run(() => onDecide([m.id], "rejected"))}><X aria-hidden /> Reject</Button>
              <Button size="sm" variant="ghost" onClick={() => setEditing(true)}><Pencil aria-hidden /> Edit</Button>
              <span className="ml-auto text-sm text-text-muted">from {m.source_label}</span>
            </div>
            {m.related.length > 0 && (
              <div className="grid gap-2 border-t border-border-subtle pt-3">
                <div className="text-sm font-semibold text-text-muted">Looks related</div>
                {m.related.map((r) => (
                  <div key={r.id} className="flex items-start gap-2 text-sm">
                    <a className="font-mono text-accent-text hover:underline" href={`#/memories/${r.id}`}>#{r.id}</a>
                    <StatusBadge status={r.status} />
                    <span className="min-w-0">{r.text}</span>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
