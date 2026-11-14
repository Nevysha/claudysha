// Talking to the dashboard server (plugin/dashboard.py). Every API call carries the random token
// from the link `claudysha dashboard` printed. The link has it in the #fragment, which never leaves
// the browser; it's kept for this tab only, then the fragment is replaced by the page route.

const TOKEN_KEY = "claudysha-token";
const TOKEN_IN_HASH = /&?token=([\w-]+)/;
let memoryToken = "";

/** Read the token from the URL (once) or from this tab's storage. */
export function readToken(): string {
  const m = location.hash.match(TOKEN_IN_HASH);
  if (m?.[1]) {
    memoryToken = m[1];
    try {
      sessionStorage.setItem(TOKEN_KEY, m[1]);
    } catch {
      /* private mode: memory only */
    }
    // Keep a route given with it (#/review&token=...), else the overview.
    const rest = location.hash.replace(/^#/, "").replace(TOKEN_IN_HASH, "");
    history.replaceState(null, "", location.pathname + "#" + (rest || "/overview"));
  }
  if (!memoryToken) {
    try {
      memoryToken = sessionStorage.getItem(TOKEN_KEY) ?? "";
    } catch {
      /* ignore */
    }
  }
  return memoryToken;
}

/** GET `path`, or POST `body` as JSON. Throws an Error with the server's message. */
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "X-Claudysha-Token": readToken() };
  const init: RequestInit = { headers };
  if (body !== undefined) {
    init.method = "POST";
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(path, init);
  let data: unknown = {};
  try {
    data = await res.json();
  } catch {
    /* empty body */
  }
  if (!res.ok) {
    const error = (data as { error?: string }).error;
    throw new Error(error || `${res.status} ${res.statusText}`);
  }
  return data as T;
}

/** Build a query string, leaving out empty values. */
export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== "" && v !== false) q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}
