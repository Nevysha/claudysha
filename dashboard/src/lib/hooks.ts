import { toast } from "@nevysha/ui";
import { useCallback, useEffect, useState } from "react";

/** Load data from `fetcher` and reload it when `deps` change. */
export function useLoad<T>(fetcher: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let live = true;
    fetcher().then(
      (d) => {
        if (!live) return;
        setData(d);
        setError(null);
      },
      (e: Error) => live && setError(e.message),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, version]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { data, error, reload, setData };
}

/** Run an action with a busy flag; a failure shows its message in a toast. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const run = useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true);
    try {
      return await fn();
    } catch (e) {
      toast({ title: "That didn't work", description: (e as Error).message, variant: "danger", duration: 8000 });
      return undefined;
    } finally {
      setBusy(false);
    }
  }, []);
  return { busy, run };
}

export async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast({ title: "Copied", variant: "success" });
  } catch {
    toast({ title: "Couldn't copy", description: "Select the text and copy it instead.", variant: "danger" });
  }
}

/** The current hash route: #/<id>/<arg>. */
export function useRoute() {
  const read = () => {
    const [id, ...rest] = location.hash.replace(/^#\/?/, "").split("/");
    return { id: id || "overview", arg: rest.join("/") };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

/** Follow the system's light/dark setting (the design system reads class="dark" on <html>). */
export function useSystemTheme() {
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => document.documentElement.classList.toggle("dark", mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);
}
