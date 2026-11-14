import { Button, Card, Checkbox, Input, Label, Table, TableBody, TableCell, TableRow } from "@nevysha/ui";
import { Ghost } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, query } from "@/lib/api";
import { date, datetime } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { Empty, NativeSelect, PageHead } from "@/ui/page";
import { getSummary } from "./api";
import { ScopeBadge, StatusBadge, TypeBadge } from "./bits";
import { MemoryDetail } from "./detail";
import type { Listing, Memory } from "./types";

type Filters = { q: string; scope: string; type: string; status: string; source: string; unused: boolean };
// Kept while the page is open, so coming back to the list finds the same filters.
let saved: Filters = { q: "", scope: "", type: "", status: "current", source: "", unused: false };
const PAGE = 50;

export function Memories({ arg }: { arg: string }) {
  const { data: summary, error, reload: reloadSummary } = useLoad(getSummary, []);
  const [filters, setFilters] = useState(saved);
  const [q, setQ] = useState(saved.q);
  const [list, setList] = useState<Listing | null>(null);
  const [current, setCurrent] = useState<number | null>(arg ? Number(arg) : null);
  const { run } = useAction();
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === q ? f : { ...f, q })), 250);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    saved = filters;
  }, [filters]);
  useEffect(() => search.current?.focus(), []);

  const fetchPage = useCallback(
    (offset: number) =>
      api<Listing>(`/api/memory/list${query({ ...filters, unused: undefined, unused_days: filters.unused ? 30 : undefined, offset, limit: PAGE })}`),
    [filters],
  );
  const load = useCallback(() => run(async () => setList(await fetchPage(0))), [run, fetchPage]);
  useEffect(() => {
    load();
  }, [load]);
  const more = () => run(async () => {
    if (!list) return;
    const r = await fetchPage(list.items.length);
    setList({ ...r, items: [...list.items, ...r.items] });
  });

  if (error) throw new Error(error);
  if (!summary) return <p className="text-text-muted">Loading…</p>;
  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const c = summary.counts;

  return (
    <>
      <PageHead title="Memories">
        {c.active} active, {c.proposed} proposed, {c.superseded} replaced, {c.rejected} rejected.
      </PageHead>
      <div className="flex flex-wrap items-center gap-2" role="search">
        <Input ref={search} type="search" aria-label="Search memories" placeholder="Search memories…" value={q} onChange={(e) => setQ(e.target.value)} className="h-10 w-auto min-w-56 flex-1" />
        <NativeSelect aria-label="Scope" value={filters.scope} onChange={(e) => set({ scope: e.target.value })}>
          <option value="">All scopes</option>
          {summary.scopes.map((s) => <option key={s.scope} value={s.scope}>{s.scope} ({s.count})</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Type" value={filters.type} onChange={(e) => set({ type: e.target.value })}>
          <option value="">All types</option>
          {summary.types.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}
        </NativeSelect>
        <NativeSelect aria-label="Status" value={filters.status} onChange={(e) => set({ status: e.target.value })}>
          <option value="current">Active + proposed</option>
          {["active", "proposed", "superseded", "rejected"].map((s) => <option key={s} value={s}>{s}</option>)}
          <option value="all">Any status</option>
        </NativeSelect>
        <NativeSelect aria-label="Source" value={filters.source} onChange={(e) => set({ source: e.target.value })}>
          <option value="">Any source</option>
          <option value="extract">From sessions</option>
          <option value="manual">Added by hand</option>
        </NativeSelect>
        <div className="flex items-center gap-2" title="never used by a session, older than 30 days">
          <Checkbox id="unused" checked={filters.unused} onCheckedChange={(v) => set({ unused: v === true })} />
          <Label htmlFor="unused">Never used</Label>
        </div>
      </div>

      <div className={current ? "grid items-start gap-4 lg:grid-cols-[3fr_2fr]" : "grid gap-4"}>
        <div className="grid min-w-0 gap-3">
          {list && (
            <p className="text-sm text-text-muted">
              {list.total ? `${list.total} memor${list.total === 1 ? "y" : "ies"}${list.ranked ? ", best matches first" : ", newest first"}` : ""}
            </p>
          )}
          {list && !list.total && <Empty icon={<Ghost aria-hidden />}>No memory matches.</Empty>}
          {list && list.total > 0 && (
            <Card className="py-0">
              <Table aria-label="Memories">
                <TableBody>
                  {list.items.map((m) => (
                    <Row key={m.id} m={m} current={m.id === current} compact={!!current} onOpen={() => setCurrent(m.id)} />
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
          {list && list.items.length < list.total && (
            <div className="flex justify-center">
              <Button variant="secondary" onClick={more}>Show more ({list.total - list.items.length} left)</Button>
            </div>
          )}
        </div>
        {current && (
          <MemoryDetail
            key={current}
            id={current}
            summary={summary}
            onOpen={setCurrent}
            onClose={() => setCurrent(null)}
            onChanged={(id) => { reloadSummary(); load(); setCurrent(id); }}
          />
        )}
      </div>
    </>
  );
}

function Row({ m, current, compact, onOpen }: { m: Memory; current: boolean; compact: boolean; onOpen: () => void }) {
  return (
    <TableRow data-state={current ? "selected" : undefined} aria-selected={current} className="cursor-pointer" onClick={onOpen}>
      <TableCell className="w-0 align-top font-mono text-text-muted">
        <button type="button" className="rounded-sm hover:text-accent-text focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none" onClick={(e) => { e.stopPropagation(); onOpen(); }} aria-label={`Open memory #${m.id}`}>
          #{m.id}
        </button>
      </TableCell>
      <TableCell className="align-top">
        <div className={compact ? "line-clamp-2" : "line-clamp-3"}>{m.text}</div>
        {(
          <div className={compact ? "mt-1 flex flex-wrap gap-1" : "mt-1 flex flex-wrap gap-1 md:hidden"}>
            <TypeBadge type={m.type} />
            {m.status !== "active" && <StatusBadge status={m.status} />}
          </div>
        )}
      </TableCell>
      {!compact && (
        <>
          <TableCell className="hidden w-0 align-top md:table-cell">
            <div className="flex flex-col items-start gap-1">
              <TypeBadge type={m.type} />
              {m.status !== "active" && <StatusBadge status={m.status} />}
            </div>
          </TableCell>
          <TableCell className="hidden w-0 align-top md:table-cell"><ScopeBadge scope={m.scope} /></TableCell>
          <TableCell className="hidden w-0 align-top text-sm whitespace-nowrap md:table-cell text-text-muted tabular-nums" title={m.last_used_at ? `last used ${datetime(m.last_used_at)}` : "never used"}>
            {date(m.created_at)}
            <br />
            used {m.use_count}×
          </TableCell>
        </>
      )}
    </TableRow>
  );
}
