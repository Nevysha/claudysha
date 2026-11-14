// Page building blocks the design system doesn't have (app-specific layout), made from its
// components and tokens only.
import { Card, CardDescription, CardHeader, CardTitle, cn, fieldClasses } from "@nevysha/ui";
import type { ComponentProps, ReactNode } from "react";

export function PageHead({ title, children, actions }: { title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="grid gap-1">
        <h1 className="text-2xl font-bold">{title}</h1>
        {children && <p className="max-w-prose text-base text-text-muted">{children}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

export function SectionTitle({ children, className }: { children: ReactNode; className?: string }) {
  return <h2 className={cn("flex flex-wrap items-center gap-2 text-lg font-semibold", className)}>{children}</h2>;
}

/** A metric: label, big value, hint. With `href`, the whole card is a link. */
export function Stat({ label, value, hint, href, accent }: { label: string; value: ReactNode; hint?: ReactNode; href?: string; accent?: boolean }) {
  const card = (
    <Card className={cn("h-full transition-colors", href && "hover:border-border-strong", accent && "border-accent bg-accent-subtle")}>
      <CardHeader>
        <CardDescription>{label}</CardDescription>
        <CardTitle className="text-2xl break-all tabular-nums">{value}</CardTitle>
        {hint && <CardDescription>{hint}</CardDescription>}
      </CardHeader>
    </Card>
  );
  return href ? (
    <a href={href} className="rounded-lg focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none">
      {card}
    </a>
  ) : (
    card
  );
}

export function StatGrid({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">{children}</div>;
}

export function Empty({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <Card className="items-center justify-center gap-2 py-12 text-center text-text-muted">
      <span className="text-2xl" aria-hidden>
        {icon}
      </span>
      <div>{children}</div>
    </Card>
  );
}

/** Preformatted text: commands, what a session receives. */
export function Code({ className, ...props }: ComponentProps<"pre">) {
  return (
    <pre
      className={cn(
        "overflow-x-auto rounded-md border border-border-subtle bg-control p-3 font-mono text-sm leading-relaxed break-words whitespace-pre-wrap text-text",
        className,
      )}
      {...props}
    />
  );
}

/** Label / value pairs. */
export function KeyValue({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[auto_1fr]", className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="font-medium text-text-muted">{k}</dt>
          <dd className="min-w-0 break-words">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export const Mono = ({ children }: { children: ReactNode }) => <span className="font-mono break-all">{children}</span>;

/**
 * A native <select> with the design system's field look. The system's Select injects a <style>
 * tag for its scroll lock, which the page's Content-Security-Policy blocks, and filters don't need it.
 */
export function NativeSelect({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(fieldClasses, "h-10 w-auto px-3", className)} {...props} />;
}

export function Note({ children }: { children: ReactNode }) {
  return <p className="rounded-md border border-warning-border bg-warning-subtle p-3 text-warning-text">{children}</p>;
}
