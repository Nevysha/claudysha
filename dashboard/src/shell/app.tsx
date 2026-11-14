import { Badge, cn, Toaster, TooltipProvider } from "@nevysha/ui";
import { Brain, Lock, TriangleAlert } from "lucide-react";
import { Component, useEffect, useState, type ReactNode } from "react";
import { api, readToken } from "@/lib/api";
import { useRoute, useSystemTheme } from "@/lib/hooks";
import { panels, type Panel } from "@/panels";
import { Empty } from "@/ui/page";
import { NavContext } from "./nav";

type Who = { user: { name: string; pronouns: string } };

export function App() {
  useSystemTheme();
  const [hasToken] = useState(() => !!readToken());
  const route = useRoute();
  const [navVersion, setNavVersion] = useState(0);
  const [who, setWho] = useState<Who["user"] | null>(null);
  const panel = panels.find((p) => p.id === route.id) ?? panels[0]!;

  useEffect(() => {
    if (hasToken) api<Who>("/api/plugin/overview").then((o) => setWho(o.user), () => {});
  }, [hasToken]);
  useEffect(() => window.scrollTo(0, 0), [route.id]);

  return (
    <TooltipProvider>
      <NavContext.Provider value={{ refresh: () => setNavVersion((v) => v + 1) }}>
        <div className="min-h-screen md:grid md:grid-cols-[auto_1fr]">
          <aside className="flex flex-col gap-4 md:w-56 border-b border-border-subtle bg-surface p-4 md:sticky md:top-0 md:h-screen md:border-r md:border-b-0">
            <a href="#/overview" className="flex items-center gap-2 px-2 text-lg font-bold">
              <Brain className="size-5 text-accent-text" aria-hidden />
              claudysha
            </a>
            <nav aria-label="Pages" className="flex flex-row flex-wrap gap-1 md:flex-col">
              {panels.map((p) => (
                <NavItem key={p.id} panel={p} active={p.id === panel.id} version={navVersion} enabled={hasToken} />
              ))}
            </nav>
            {who && (
              <div className="mt-auto hidden px-2 text-sm text-text-muted md:block">
                {who.name} ({who.pronouns})
              </div>
            )}
          </aside>
          <main className="mx-auto grid w-full max-w-6xl min-w-0 content-start gap-6 p-4 md:p-8">
            {hasToken ? (
              <Boundary key={panel.id}>
                <panel.Page arg={route.arg} />
              </Boundary>
            ) : (
              <Empty icon={<Lock aria-hidden />}>
                Open the link that <code className="font-mono">claudysha dashboard</code> printed in your terminal: it carries the key to this page.
              </Empty>
            )}
          </main>
        </div>
        <Toaster />
      </NavContext.Provider>
    </TooltipProvider>
  );
}

function NavItem({ panel, active, version, enabled }: { panel: Panel; active: boolean; version: number; enabled: boolean }) {
  const [count, setCount] = useState(0);
  useEffect(() => {
    if (enabled && panel.badge) panel.badge().then(setCount, () => {});
  }, [panel, version, enabled]);
  const Icon = panel.icon;
  return (
    <a
      href={`#/${panel.id}`}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-2 rounded-md px-2 py-2 text-base whitespace-nowrap text-text-muted transition-colors hover:bg-control-hover hover:text-text",
        "focus-visible:ring-2 focus-visible:ring-focus focus-visible:outline-none",
        active && "bg-accent-subtle font-semibold text-accent-text",
      )}
    >
      <Icon className="size-4" aria-hidden />
      <span>{panel.title}</span>
      {count > 0 && (
        <Badge variant="accent" className="ml-auto" aria-label={`${count} waiting`}>
          {count}
        </Badge>
      )}
    </a>
  );
}

/** A page that fails to load shows its error instead of a blank screen. */
class Boundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    return this.state.error ? <Empty icon={<TriangleAlert aria-hidden />}>{this.state.error.message}</Empty> : this.props.children;
  }
}
