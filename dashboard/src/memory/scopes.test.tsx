import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Scopes } from "./scopes";

const scopes = {
  global: "global",
  scopes: [
    { scope: "global", active: 4, proposed: 0, total: 4, aliases: [] },
    { scope: "/srv/notes", active: 2, proposed: 1, total: 3, aliases: [] },
    { scope: "tools", active: 1, proposed: 0, total: 1, aliases: ["old-tools"] },
  ],
  history: [
    { id: 1, at: "2026-11-15T10:00:00", kind: "alias", old: "old-tools", new: "tools", memory_ids: [7], aliases: [["old-tools", null, "tools"]], undone_at: null, label: "" },
  ],
};
const plan = { kind: "rename", old: "/srv/notes", new: "notes", count: 3, aliases: [["/srv/notes", null, "notes"]] };

describe("Scopes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("previews a rename, then applies it", async () => {
    const fetch = vi.fn(async (url: string) => {
      const body = url === "/api/memory/scopes" ? scopes : url === "/api/memory/scope/apply" ? { ...plan, id: 2, moved: 3 } : plan;
      return new Response(JSON.stringify(body), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<Scopes />);
    expect(await screen.findByText("old-tools")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Rename/ })).toHaveLength(2); // not for global
    await userEvent.click(screen.getAllByRole("button", { name: /Rename/ })[0]!);
    await userEvent.type(screen.getByLabelText("New name"), "notes");
    await userEvent.click(screen.getByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/3 memories move/)).toBeInTheDocument();
    expect(fetch.mock.calls.some(([url]) => url === "/api/memory/scope/apply")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/memory/scope/apply")).toBe(true));
    const post = fetch.mock.calls.find(([url]) => url === "/api/memory/scope/apply")!;
    expect(JSON.parse(String((post as unknown as [string, RequestInit])[1].body))).toEqual({ kind: "rename", old: "/srv/notes", new: "notes", alias: true });
  });

  it("asks before undoing a change", async () => {
    const fetch = vi.fn(async (url: string) =>
      new Response(JSON.stringify(url === "/api/memory/scopes" ? scopes : { id: 1, moved_back: 1, kept: 0, aliases_skipped: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    render(<Scopes />);
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(fetch.mock.calls.some(([url]) => url === "/api/memory/scope/undo")).toBe(false);
    await userEvent.click(screen.getByRole("button", { name: "Undo #1" }));
    await waitFor(() => expect(fetch.mock.calls.some(([url]) => url === "/api/memory/scope/undo")).toBe(true));
  });
});
