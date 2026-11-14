import { Badge, Button, Input, Label, Textarea } from "@nevysha/ui";
import { useId, useState } from "react";
import { useAction } from "@/lib/hooks";
import { NativeSelect } from "@/ui/page";
import { editMemory } from "./api";
import type { Memory, Status, Summary } from "./types";

export const TypeBadge = ({ type }: { type: string }) => <Badge variant="accent">{type.replace("_", " ")}</Badge>;

export const ScopeBadge = ({ scope }: { scope: string }) => (
  <Badge className="max-w-64 truncate font-mono font-normal" title={scope}>
    {scope}
  </Badge>
);

const statusVariant = { active: "success", proposed: "accent", superseded: "neutral", rejected: "danger" } as const;
export const StatusBadge = ({ status }: { status: Status }) => <Badge variant={statusVariant[status]}>{status}</Badge>;

/** Edit a memory in place. `full` adds tags and confidence. */
export function EditForm({ m, summary, full, onSaved, onCancel }: { m: Memory; summary: Summary; full?: boolean; onSaved: (m: Memory) => void; onCancel: () => void }) {
  const id = useId();
  const [text, setText] = useState(m.text);
  const [type, setType] = useState(m.type);
  const [scope, setScope] = useState(m.scope);
  const [tags, setTags] = useState(m.tags);
  const [confidence, setConfidence] = useState(String(m.confidence));
  const { busy, run } = useAction();

  const save = () =>
    run(async () => {
      const body: Parameters<typeof editMemory>[0] = { id: m.id };
      if (text.trim() !== m.text) body.text = text;
      if (type !== m.type) body.type = type;
      if (scope.trim() !== m.scope) body.scope = scope;
      if (full && tags !== m.tags) body.tags = tags;
      if (full && Number(confidence) !== m.confidence) body.confidence = Number(confidence);
      if (Object.keys(body).length === 1) return onCancel();
      onSaved((await editMemory(body)).item);
    });

  return (
    <div className="grid gap-3">
      <div className="grid gap-1">
        <Label htmlFor={`${id}-text`}>Text</Label>
        <Textarea id={`${id}-text`} rows={4} value={text} onChange={(e) => setText(e.target.value)} />
      </div>
      <div className="flex flex-wrap gap-3">
        <div className="grid gap-1">
          <Label htmlFor={`${id}-type`}>Type</Label>
          <NativeSelect id={`${id}-type`} value={type} onChange={(e) => setType(e.target.value)}>
            {summary.types.map((t) => (
              <option key={t} value={t}>{t.replace("_", " ")}</option>
            ))}
          </NativeSelect>
        </div>
        <div className="grid min-w-48 flex-1 gap-1">
          <Label htmlFor={`${id}-scope`}>Scope</Label>
          <Input id={`${id}-scope`} list={`${id}-scopes`} value={scope} onChange={(e) => setScope(e.target.value)} placeholder="global for every session" />
          <datalist id={`${id}-scopes`}>
            {summary.scopes.map((s) => (
              <option key={s.scope} value={s.scope} />
            ))}
          </datalist>
        </div>
        {full && (
          <>
            <div className="grid min-w-32 flex-1 gap-1">
              <Label htmlFor={`${id}-tags`}>Tags</Label>
              <Input id={`${id}-tags`} value={tags} onChange={(e) => setTags(e.target.value)} />
            </div>
            <div className="grid w-28 gap-1">
              <Label htmlFor={`${id}-conf`}>Confidence</Label>
              <Input id={`${id}-conf`} type="number" min={0} max={1} step={0.1} value={confidence} onChange={(e) => setConfidence(e.target.value)} />
            </div>
          </>
        )}
      </div>
      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={busy}>Save</Button>
        <Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </div>
  );
}
