---
name: nevysha-ui
description: Build React UI with Nevysha's design system (@nevysha/ui). Use whenever you create or change UI in an app that depends on @nevysha/ui - pages, forms, dialogs, buttons, layout, colors, spacing - so the result uses the system's components and tokens instead of custom markup or raw CSS values.
---

# @nevysha/ui

React 19 + Tailwind v4 design system. Components are shadcn-style (Radix + Tailwind), styled only with semantic tokens.

## Setup (once per app)

```tsx
// app root (main.tsx / layout.tsx)
import "@nevysha/ui/font.css";   // Montserrat
import "@nevysha/ui/styles.css"; // tokens + component styles
import { Toaster, TooltipProvider } from "@nevysha/ui";

<TooltipProvider>
  <App />
  <Toaster />
</TooltipProvider>
```

Dark mode: `class="dark"` (or `data-theme="dark"`) on `<html>`.

**If the app runs Tailwind v4 itself** (to write its own layout classes like `gap-6` or `text-text-muted`), import the theme instead of `styles.css`, in the app's CSS entry:

```css
@import "tailwindcss";
@import "@nevysha/ui/theme.css"; /* tokens, dark variant, base styles; scans the components too */
```

Without Tailwind in the app, only the classes the components themselves use exist, so write app layout with components or plain CSS on the `--ds-*` variables.

## Rules

1. **Use a component before writing markup.** Catalog below. If nothing fits, compose existing ones; only then write new markup, styled with tokens.
2. **Only semantic token classes.** The default Tailwind palette does not exist (`bg-blue-500`, `text-gray-600` do nothing).
   - Backgrounds: `bg-bg` (page), `bg-surface` (cards, fields), `bg-control`, `bg-control-hover`, `bg-accent-subtle`
   - Text: `text-text`, `text-text-muted`, `text-accent-text`, `text-danger-text`, `text-success-text`, `text-warning-text`, `text-on-accent`
   - Borders: `border-border-subtle` (dividers, cards), `border-border` (fields), `border-border-strong`
   - Brand / status fills: `bg-accent`, `bg-danger`; subtle fills `bg-success-subtle`, `bg-warning-subtle`, `bg-danger-subtle`
   - Focus: `ring-focus`. Backdrop: `bg-overlay`.
3. **Spacing on the grid:** `p-*`, `m-*`, `gap-*` with the normal Tailwind numbers (1, 2, 3, 4, 6, 8, 12, 16). Never `p-[13px]`.
4. **Radius:** `rounded-sm` (badges, small), `rounded-md` (controls), `rounded-lg` (cards, dialogs), `rounded-full` (pills, avatars).
5. **Type:** `text-sm`, `text-base`, `text-lg`, `text-xl`, `text-2xl`; weights `font-medium`, `font-semibold`, `font-bold`.
6. **No raw values:** no hex/rgb/oklch, no arbitrary values (`bg-[#...]`, `text-[17px]`), no inline `style` colors.
7. **Accessibility:** every field has a visible `Label`; icon-only buttons have `aria-label` (and a Tooltip); errors use `aria-invalid` + `aria-describedby`; every Dialog has a `DialogTitle`.
8. Token values in JS (charts, canvas): `import { colors, cssVar } from "@nevysha/ui/tokens"`.

## Component catalog

| Component | Import | Use for |
|---|---|---|
| `Button` | `Button`, `buttonVariants` | Actions. `variant`: primary (one per area), secondary, ghost, danger. `size`: sm, md, lg, icon. `asChild` to style a link. |
| `Input` | `Input`, `fieldClasses` | Single-line text. `fieldClasses` gives custom fields the same look. |
| `Textarea` | `Textarea` | Multi-line text. |
| `Label` | `Label` | Label for any field (`htmlFor` = field `id`). |
| `Checkbox` | `Checkbox` | Choices submitted with a form; `checked="indeterminate"` for select-all. |
| `Switch` | `Switch` | Settings that apply immediately. |
| `Select` | `Select`, `SelectTrigger`, `SelectValue`, `SelectContent`, `SelectItem`, `SelectGroup`, `SelectLabel`, `SelectSeparator` | Pick one of ~5-15 options. |
| `Badge` | `Badge`, `badgeVariants` | Status labels. `variant`: neutral, accent, success, warning, danger. Not clickable. |
| `Card` | `Card`, `CardHeader`, `CardTitle`, `CardDescription`, `CardContent`, `CardFooter` | Group related content. Don't nest cards. |
| `Table` | `Table`, `TableHeader`, `TableBody`, `TableFooter`, `TableRow`, `TableHead`, `TableCell`, `TableCaption` | Rows of records with the same fields. `data-state="selected"` on the chosen row. Name it with a caption or `aria-label`. Not for layout. |
| `Dialog` | `Dialog`, `DialogTrigger`, `DialogContent`, `DialogHeader`, `DialogTitle`, `DialogDescription`, `DialogFooter`, `DialogClose` | Confirm destructive actions, short focused tasks. |
| `Tabs` | `Tabs`, `TabsList`, `TabsTrigger`, `TabsContent` | Views of one object. Not for navigation or steps. |
| `Tooltip` | `TooltipProvider`, `Tooltip`, `TooltipTrigger`, `TooltipContent` | Name icon-only buttons. Never essential info. |
| `Toast` | `Toaster`, `toast`, `dismissToast`, `dismissAllToasts` | Brief "it happened" messages. Not for errors to fix or decisions. |
| `cn` | `cn` | Merge class names (later Tailwind classes win). |

Full props, states and examples: run the design system's Storybook (`pnpm dev` in the design-system repo) - it also serves an MCP endpoint at http://localhost:6006/mcp.

## Missing something?

If no component fits, or you find a bug: don't patch the design system's code inside `node_modules`, and don't fork its styles into the app. Follow [CONTRIBUTING.md](https://github.com/Nevysha/design-system/blob/main/CONTRIBUTING.md) (also at `node_modules/@nevysha/ui/CONTRIBUTING.md`): suggest the change in an issue, or implement it in a pull request. Meanwhile, compose existing components with tokens only and mark the spot with `// TODO(@nevysha/ui): <issue link>`.

## Patterns

**Form field with error**

```tsx
<div className="grid gap-2">
  <Label htmlFor="email">Owner email</Label>
  <Input id="email" type="email" aria-invalid={!!error} aria-describedby="email-error" />
  {error && <p id="email-error" className="text-sm text-danger-text">{error}</p>}
</div>
```

**Confirm a destructive action**

```tsx
<Dialog>
  <DialogTrigger asChild><Button variant="danger">Delete</Button></DialogTrigger>
  <DialogContent>
    <DialogHeader>
      <DialogTitle>Delete this project?</DialogTitle>
      <DialogDescription>This can't be undone.</DialogDescription>
    </DialogHeader>
    <DialogFooter>
      <DialogClose asChild><Button variant="secondary">Cancel</Button></DialogClose>
      <Button variant="danger" onClick={onDelete}>Delete</Button>
    </DialogFooter>
  </DialogContent>
</Dialog>
```

**After an action:** `toast({ title: "Project archived", variant: "success" })`.

**Page layout:** `bg-bg` page, content in `Card`s or plain sections separated by `gap-6`/`gap-8`, max width with `mx-auto max-w-5xl px-4`.
