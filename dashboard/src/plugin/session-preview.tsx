import { Badge, Card, Label, Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@nevysha/ui";
import { useState } from "react";
import { api, query } from "@/lib/api";
import { useLoad } from "@/lib/hooks";
import { Code, NativeSelect, Note, PageHead, SectionTitle, Stat, StatGrid } from "@/ui/page";

type Section = { title: string; kind: "hook" | "skills"; source: string; text: string; error: string; tokens: number };
type OnDemand = { title: string; source: string; when: string; text: string; tokens: number };
type Preview = {
  scope: string | null;
  shown_scope: string;
  sections: Section[];
  on_demand: OnDemand[];
  total: number;
  scopes: { scope: string; count: number }[];
};

const anchor = (source: string) => `sec-${source.replace(/[^\w-]/g, "-")}`;
const jump = (source: string) => (e: React.MouseEvent) => {
  e.preventDefault();
  document.getElementById(anchor(source))?.scrollIntoView({ behavior: "smooth" });
};

export function SessionPreview() {
  const [scope, setScope] = useState("");
  const [scopes, setScopes] = useState<Preview["scopes"]>([]);
  const { data: p, error } = useLoad(async () => {
    const r = await api<Preview>(`/api/plugin/session-preview${query({ scope })}`);
    setScopes((s) => (s.length ? s : r.scopes));
    return r;
  }, [scope]);
  const loading = !p || (p.scope ?? "") !== scope;

  return (
    <>
      <PageHead
        title="Session preview"
        actions={
          <div className="flex items-center gap-2">
            <Label htmlFor="preview-scope">Session in</Label>
            <NativeSelect id="preview-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="">A new folder (fresh session)</option>
              {scopes.filter((s) => s.scope !== "global").map((s) => (
                <option key={s.scope} value={s.scope}>{s.scope} ({s.count})</option>
              ))}
            </NativeSelect>
          </div>
        }
      >
        What claudysha adds when a Claude Code session starts, exactly as the session receives it.
      </PageHead>
      {error ? <Note>{error}</Note> : loading ? <p className="text-text-muted">Running the session start…</p> : <Body p={p} />}
    </>
  );
}

function Body({ p }: { p: Preview }) {
  const max = Math.max(1, ...p.sections.map((s) => s.tokens));
  return (
    <>
      <StatGrid>
        <Stat accent label="Added to every session start" value={`≈ ${p.total.toLocaleString()}`} hint="tokens, estimated" />
        <Stat
          label="Scope shown"
          value={<span className="font-mono text-lg">{p.scope || "new folder"}</span>}
          hint={p.scope ? "a session working in this repository or folder" : `a folder claudysha has never seen (${p.shown_scope})`}
        />
      </StatGrid>

      <section className="grid gap-3">
        <SectionTitle>Where the tokens go</SectionTitle>
        <Card className="py-0">
          <Table aria-label="Tokens per part of the session start">
            <TableHeader>
              <TableRow>
                <TableHead>Part</TableHead>
                <TableHead className="w-1/2"><span className="sr-only">Share bar</span></TableHead>
                <TableHead className="text-right">Tokens</TableHead>
                <TableHead className="text-right">Share</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {p.sections.map((s) => (
                <TableRow key={s.source}>
                  <TableCell><a className="text-accent-text hover:underline" href={`#${anchor(s.source)}`} onClick={jump(s.source)}>{s.title}</a></TableCell>
                  <TableCell title={`${s.tokens} tokens`}>
                    <div className="h-2 overflow-hidden rounded-full bg-control">
                      <div className="h-full rounded-full bg-accent" style={{ width: `${Math.max((s.tokens / max) * 100, s.tokens > 0 ? 1 : 0)}%` }} />
                    </div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{s.tokens ? `≈ ${s.tokens.toLocaleString()}` : <span className="text-text-muted">nothing</span>}</TableCell>
                  <TableCell className="text-right text-text-muted tabular-nums">{p.total ? `${Math.round((100 * s.tokens) / p.total)}%` : ""}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
        <p className="text-sm text-text-muted">
          Estimates: claudysha has no tokenizer, so counts can be off by about 15%. Claude Code adds its own system prompt and tools on top; this is only what
          claudysha adds. A skill's full instructions load only when it's used.
        </p>
      </section>

      {p.sections.map((s) => (
        <section key={s.source} id={anchor(s.source)} className="grid scroll-mt-4 gap-3">
          <SectionTitle>
            {s.title}
            <Badge>{s.tokens ? `≈ ${s.tokens} tokens` : "prints nothing"}</Badge>
            <span className="font-mono text-sm font-normal text-text-muted">{s.source}</span>
          </SectionTitle>
          {s.error && <Note>This part failed: {s.error}</Note>}
          {s.text ? (
            <Code>{s.text}</Code>
          ) : (
            <p className="text-sm text-text-muted">
              {s.kind === "hook" ? "Nothing printed for this session (for example, no backlog item is due, or dev mode is off)." : "No skill listed."}
            </p>
          )}
        </section>
      ))}

      <PageHead title="Loaded when used">
        Not part of the session start, so not in the total above. A skill's full instructions join the session the first time it's used, and stay for the
        rest of it. This is where sessions learn the memory commands.
      </PageHead>
      {p.on_demand.length > 0 && (
        <Card className="py-0">
          <Table aria-label="Skills loaded when used">
            <TableHeader>
              <TableRow>
                <TableHead>Skill</TableHead>
                <TableHead>Loads</TableHead>
                <TableHead className="text-right">Tokens</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {p.on_demand.map((s) => (
                <TableRow key={s.source}>
                  <TableCell><a className="text-accent-text hover:underline" href={`#${anchor(s.source)}`} onClick={jump(s.source)}>{s.title}</a></TableCell>
                  <TableCell className="text-text-muted">{s.when}</TableCell>
                  <TableCell className="text-right tabular-nums">≈ {s.tokens.toLocaleString()}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
      {p.on_demand.map((s) => (
        <section key={s.source} id={anchor(s.source)} className="grid scroll-mt-4 gap-3">
          <SectionTitle>
            {s.title}
            <Badge>≈ {s.tokens} tokens</Badge>
            <span className="text-sm font-normal text-text-muted">{s.when}</span>
          </SectionTitle>
          <Code>{s.text}</Code>
        </section>
      ))}
    </>
  );
}
