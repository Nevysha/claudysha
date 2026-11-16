import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Checkbox, Input, Label, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, toast } from "@nevysha/ui";
import { Link2, Pencil, Undo2, X } from "lucide-react";
import { useId, useState } from "react";
import { datetime, plural } from "@/lib/format";
import { useAction, useLoad } from "@/lib/hooks";
import { useNav } from "@/shell/nav";
import { PageHead } from "@/ui/page";
import { applyScope, getScopes, planScope, undoScope } from "./api";
import type { ScopeChange, ScopePlan, ScopeRequest, ScopeRow } from "./types";

// A rename or alias is shown first (what moves, which aliases change), then applied on a second
// click. No dialog: the page's Content-Security-Policy blocks the style tag its scroll lock injects.
type Editing = { scope: string; kind: "rename" | "alias" } | null;

export function Scopes() {
  const { data, error, reload } = useLoad(getScopes, []);
  const [editing, setEditing] = useState<Editing>(null);
  const nav = useNav();
  const { busy, run } = useAction();
  if (error) throw new Error(error);
  if (!data) return <p className="text-text-muted">Loading…</p>;

  const changed = () => {
    setEditing(null);
    reload();
    nav.refresh();
  };
  const unalias = (alias: string) =>
    run(async () => {
      const r = await applyScope({ kind: "unalias", old: alias });
      toast({ title: `${alias} is its own scope again`, description: `Its memories stay in ${r.new}. Undo it below (#${r.id}).`, variant: "success" });
      changed();
    });
  const names = data.scopes.map((s) => s.scope);

  return (
    <>
      <PageHead title="Scopes">
        Where each memory applies: a repository, a folder, or global for every session. Rename a scope, or make a name an alias of another
        scope so sessions there share its memories. Every change can be undone.
      </PageHead>
      <Card className="py-0">
        <Table aria-label="Scopes">
          <TableHeader>
            <TableRow>
              <TableHead>Scope</TableHead>
              <TableHead className="w-0 text-right">Memories</TableHead>
              <TableHead className="w-0"><span className="sr-only">Actions</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {data.scopes.map((s) => (
              <ScopeRowView
                key={s.scope}
                s={s}
                isGlobal={s.scope === data.global}
                editing={editing?.scope === s.scope ? editing.kind : null}
                onEdit={(kind) => setEditing({ scope: s.scope, kind })}
                onCancel={() => setEditing(null)}
                onDone={changed}
                onUnalias={unalias}
                busy={busy}
                names={names}
              />
            ))}
          </TableBody>
        </Table>
      </Card>
      <History items={data.history} onUndone={changed} />
    </>
  );
}

type RowProps = {
  s: ScopeRow;
  isGlobal: boolean;
  editing: "rename" | "alias" | null;
  onEdit: (kind: "rename" | "alias") => void;
  onCancel: () => void;
  onDone: () => void;
  onUnalias: (alias: string) => void;
  busy: boolean;
  names: string[];
};

function ScopeRowView({ s, isGlobal, editing, onEdit, onCancel, onDone, onUnalias, busy, names }: RowProps) {
  const counts = [`${s.active} active`, s.proposed ? `${s.proposed} proposed` : "", s.total - s.active - s.proposed ? `${s.total - s.active - s.proposed} replaced` : ""].filter(Boolean);
  return (
    <>
      <TableRow data-state={editing ? "selected" : undefined}>
        <TableCell className="align-top">
          <div className="font-mono break-all">{s.scope}</div>
          {s.aliases.length > 0 && (
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <span className="text-sm text-text-muted">also:</span>
              {s.aliases.map((a) => (
                <Badge key={a} className="gap-1 font-mono font-normal" title={`sessions in ${a} use ${s.scope}`}>
                  <span className="max-w-64 truncate">{a}</span>
                  <button type="button" aria-label={`Remove alias ${a}`} title="Remove this alias" disabled={busy} className="rounded-sm hover:text-danger-text focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none" onClick={() => onUnalias(a)}>
                    <X className="size-3" aria-hidden />
                  </button>
                </Badge>
              ))}
            </div>
          )}
        </TableCell>
        <TableCell className="align-top text-right text-sm whitespace-nowrap text-text-muted tabular-nums">{counts.join(", ")}</TableCell>
        <TableCell className="align-top">
          {!isGlobal && (
            <div className="flex justify-end gap-1">
              <Button size="sm" variant="ghost" onClick={() => onEdit("rename")} aria-pressed={editing === "rename"} title="Give this scope another name"><Pencil aria-hidden /> Rename</Button>
              <Button size="sm" variant="ghost" onClick={() => onEdit("alias")} aria-pressed={editing === "alias"} title="Make this name point at another scope"><Link2 aria-hidden /> Alias to…</Button>
            </div>
          )}
        </TableCell>
      </TableRow>
      {editing && (
        <TableRow>
          <TableCell colSpan={3} className="bg-control">
            <ChangeForm key={editing} scope={s.scope} kind={editing} names={names.filter((n) => n !== s.scope)} onCancel={onCancel} onDone={onDone} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function ChangeForm({ scope, kind, names, onCancel, onDone }: { scope: string; kind: "rename" | "alias"; names: string[]; onCancel: () => void; onDone: () => void }) {
  const id = useId();
  const [target, setTarget] = useState("");
  const [keepAlias, setKeepAlias] = useState(true);
  const [plan, setPlan] = useState<ScopePlan | null>(null);
  const { busy, run } = useAction();
  const request = (): ScopeRequest => ({ kind, old: scope, new: target.trim(), alias: kind === "alias" || keepAlias });
  const preview = () => run(async () => setPlan(await planScope(request())));
  const apply = () =>
    run(async () => {
      const r = await applyScope(request());
      toast({ title: kind === "rename" ? `Renamed to ${r.new}` : `${scope} now points at ${r.new}`, description: `${plural(r.moved, "memory", "memories")} moved. Undo it below (#${r.id}).`, variant: "success" });
      onDone();
    });
  const edit = (f: () => void) => { f(); setPlan(null); };

  return (
    <div className="grid gap-3">
      <p className="text-sm text-text-muted">
        {kind === "rename"
          ? "Every memory of this scope moves to the new name. If that scope exists, they join it."
          : "Sessions in this repository or folder will use the other scope, and this scope's memories move there."}
      </p>
      <div className="flex flex-wrap items-end gap-3">
        <div className="grid min-w-64 flex-1 gap-1">
          <Label htmlFor={`${id}-target`}>{kind === "rename" ? "New name" : "Scope it points at"}</Label>
          <Input id={`${id}-target`} list={`${id}-names`} value={target} autoFocus onChange={(e) => edit(() => setTarget(e.target.value))} onKeyDown={(e) => e.key === "Enter" && target.trim() && preview()} />
          <datalist id={`${id}-names`}>
            {names.map((n) => <option key={n} value={n} />)}
          </datalist>
        </div>
        {kind === "rename" && (
          <div className="flex h-10 items-center gap-2" title="Sessions still detected under the old name keep landing in the new one. Untick it only if the old name will never come back (a repository renamed).">
            <Checkbox id={`${id}-alias`} checked={keepAlias} onCheckedChange={(v) => edit(() => setKeepAlias(v === true))} />
            <Label htmlFor={`${id}-alias`}>Keep the old name as an alias</Label>
          </div>
        )}
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" disabled={busy || !target.trim()} onClick={preview}>Preview</Button>
          <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
        </div>
      </div>
      {plan && (
        <div className="grid gap-2 rounded-md border border-border-subtle bg-surface p-3" role="status">
          <ul className="grid list-disc gap-1 pl-6 text-sm">
            {plan.count > 0 && <li>{plural(plan.count, "memory moves", "memories move")} from <code className="font-mono">{plan.old}</code> to <code className="font-mono">{plan.new}</code>{plan.new !== target.trim() && ` (${target.trim()} is an alias of it)`}</li>}
            {plan.aliases.map(([name, before, after]) => (
              <li key={name}>
                <code className="font-mono">{name}</code> becomes an alias of <code className="font-mono">{after}</code>
                {before && <span className="text-text-muted"> (was of {before})</span>}
              </li>
            ))}
          </ul>
          <div>
            <Button size="sm" disabled={busy} onClick={apply}>Apply</Button>
          </div>
        </div>
      )}
    </div>
  );
}

function History({ items, onUndone }: { items: ScopeChange[]; onUndone: () => void }) {
  const [confirming, setConfirming] = useState<number | null>(null);
  const { busy, run } = useAction();
  if (!items.length) return null;
  const undo = (c: ScopeChange) =>
    run(async () => {
      const r = await undoScope(c.id);
      const extra = [r.kept ? `${plural(r.kept, "memory", "memories")} moved since stayed` : "", r.aliases_skipped.length ? `aliases changed since kept: ${r.aliases_skipped.join(", ")}` : ""].filter(Boolean).join("; ");
      toast({ title: `Undone #${c.id}`, description: `${plural(r.moved_back, "memory", "memories")} back in ${c.old}${extra ? `. ${extra}` : ""}.`, variant: "success" });
      setConfirming(null);
      onUndone();
    });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent changes</CardTitle>
        <CardDescription>Undoing a change moves its memories back, except those moved again since.</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2">
          {items.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono text-text-muted">#{c.id}</span>
              <span className="text-text-muted tabular-nums">{datetime(c.at)}</span>
              <span className={c.undone_at ? "min-w-0 break-all text-text-muted line-through" : "min-w-0 break-all"}>{describe(c)}</span>
              {c.undone_at ? (
                <Badge>undone {datetime(c.undone_at)}</Badge>
              ) : confirming === c.id ? (
                <span className="flex gap-1">
                  <Button size="sm" variant="danger" disabled={busy} onClick={() => undo(c)}>Undo #{c.id}</Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>Keep</Button>
                </span>
              ) : (
                <Button size="sm" variant="ghost" onClick={() => setConfirming(c.id)}><Undo2 aria-hidden /> Undo</Button>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function describe(c: ScopeChange) {
  const n = c.memory_ids.length ? `, ${plural(c.memory_ids.length, "memory", "memories")}` : "";
  if (c.kind === "rename") return `Renamed ${c.old} to ${c.new}${n}`;
  if (c.kind === "alias") return `${c.old} became an alias of ${c.new}${n}`;
  return `Removed the alias ${c.old} (of ${c.new})`;
}
