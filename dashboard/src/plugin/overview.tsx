import { Badge, Button, Card, CardContent, CardDescription, CardHeader, CardTitle, Table, TableBody, TableCell, TableHead, TableHeader, TableRow, toast } from "@nevysha/ui";
import { Archive, Copy } from "lucide-react";
import { useState } from "react";
import { api, query } from "@/lib/api";
import { copy, useAction, useLoad } from "@/lib/hooks";
import { datetime, size } from "@/lib/format";
import { Code, KeyValue, Mono, Note, PageHead, SectionTitle, Stat, StatGrid } from "@/ui/page";

type ArchiveEntry = { name: string; kind: string; path: string; size: number; modified: string };
type OverviewData = {
  user: { name: string; pronouns: string };
  cards: { label: string; value: number | string; hint?: string; panel?: string; accent?: boolean; module: string }[];
  settings: Record<string, string>;
  config: { path: string; exists: boolean };
  data: { path: string; size: number };
  dev_mode: boolean;
  guard: { paused_until: string | null };
  archives: ArchiveEntry[];
  archives_dir: string;
};
type LoadPreview = { archive: ArchiveEntry; created?: string; files: number; erased: string[]; command: string };

export function Overview() {
  const { data: o, error, setData } = useLoad(() => api<OverviewData>("/api/plugin/overview"), []);
  const archiving = useAction();
  if (error) throw new Error(error);
  if (!o) return <p className="text-text-muted">Loading…</p>;

  const archiveNow = () =>
    archiving.run(async () => {
      const r = await api<{ path: string; archives: ArchiveEntry[] }>("/api/plugin/archive", {});
      setData({ ...o, archives: r.archives });
      toast({ title: "Archived", description: r.path, variant: "success" });
    });

  return (
    <>
      <PageHead title={`Hello, ${o.user.name}`}>What claudysha remembers, and how it's set up.</PageHead>
      <StatGrid>
        {o.cards.map((c) => (
          <Stat key={c.label} label={c.label} value={c.value} hint={c.hint} href={c.panel && `#/${c.panel}`} accent={c.accent} />
        ))}
      </StatGrid>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Settings</CardTitle>
            <CardDescription>Edit them in the settings file, or with claudysha setup.</CardDescription>
          </CardHeader>
          <CardContent>
            <KeyValue items={Object.entries(o.settings).map(([k, v]) => [k, <Mono>{v}</Mono>])} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Status</CardTitle>
          </CardHeader>
          <CardContent>
            <KeyValue
              items={[
                ["Settings file", <span className="flex flex-wrap items-center gap-2"><Mono>{o.config.path}</Mono>{!o.config.exists && <Badge variant="warning">missing: defaults</Badge>}</span>],
                ["Data folder", <span className="flex flex-wrap items-center gap-2"><Mono>{o.data.path}</Mono><span className="text-text-muted">{size(o.data.size)}</span></span>],
                ["Dev mode", <span className="flex flex-wrap items-center gap-2">{o.dev_mode ? <Badge variant="warning">on</Badge> : <Badge>off</Badge>}<span className="text-sm text-text-muted">changed only in your terminal: claudysha dev on/off</span></span>],
                ["Guard", o.guard.paused_until ? <Badge variant="warning">paused until {o.guard.paused_until.slice(11)}</Badge> : <Badge variant="success">on</Badge>],
              ]}
            />
          </CardContent>
        </Card>
      </div>

      <Archives archives={o.archives} dir={o.archives_dir} onArchive={archiveNow} busy={archiving.busy} />
    </>
  );
}

function Archives({ archives, dir, onArchive, busy }: { archives: ArchiveEntry[]; dir: string; onArchive: () => void; busy: boolean }) {
  const [preview, setPreview] = useState<LoadPreview | null>(null);
  const loading = useAction();
  const show = (name: string) =>
    loading.run(async () => {
      setPreview(await api<LoadPreview>(`/api/plugin/load-preview${query({ name })}`));
      requestAnimationFrame(() => document.getElementById("restore")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
    });

  return (
    <section className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionTitle>Archives</SectionTitle>
        <Button onClick={onArchive} disabled={busy}>
          <Archive aria-hidden /> Archive now
        </Button>
      </div>
      <Card className="py-0">
        {archives.length ? (
          <Table aria-label="Archives">
            <TableHeader>
              <TableRow>
                <TableHead>Archive</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead>Date</TableHead>
                <TableHead>Size</TableHead>
                <TableHead><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {archives.map((a) => (
                <TableRow key={a.path} data-state={preview?.archive.name === a.name ? "selected" : undefined}>
                  <TableCell><Mono>{a.name}</Mono></TableCell>
                  <TableCell><Badge variant={a.kind === "archive" ? "neutral" : "warning"}>{a.kind}</Badge></TableCell>
                  <TableCell className="whitespace-nowrap tabular-nums">{datetime(a.modified)}</TableCell>
                  <TableCell className="whitespace-nowrap tabular-nums">{size(a.size)}</TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="ghost" onClick={() => show(a.name)} disabled={loading.busy}>Restore…</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : (
          <p className="p-5 text-text-muted">No archive yet.</p>
        )}
      </Card>
      <p className="text-sm text-text-muted">New archives go to {dir}. Copy them somewhere safe for a real backup.</p>

      {preview && (
        <Card id="restore">
          <CardHeader>
            <CardTitle>Restore {preview.archive.name}</CardTitle>
            <CardDescription>
              Made {datetime(preview.created)}, {preview.files} files. Restoring replaces all of claudysha's data with it
              {preview.erased.length ? `, and erases ${preview.erased.length} current file(s) that aren't in it:` : "."}
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-3">
            {preview.erased.length > 0 && (
              <Code>{preview.erased.slice(0, 20).join("\n") + (preview.erased.length > 20 ? `\n… and ${preview.erased.length - 20} more` : "")}</Code>
            )}
            <Note>
              Restoring is never done from this page: it needs you to type erase in your own terminal, so no program (and no Claude session) can do it for you. Your current data is saved first.
            </Note>
            <div className="flex flex-wrap items-start gap-2">
              <Code className="flex-1">{preview.command}</Code>
              <Button variant="secondary" onClick={() => copy(preview.command)}>
                <Copy aria-hidden /> Copy
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </section>
  );
}
