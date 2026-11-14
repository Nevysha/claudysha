import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Review } from "./review";

const summary = { counts: { active: 3, proposed: 2, superseded: 0, rejected: 0 }, scopes: [], types: ["fact", "preference"], needs_approval: [], global: "global" };
const memory = (id: number, text: string) => ({
  id, text, type: "preference", scope: "global", status: "proposed", tags: "", confidence: 0.9, source: "manual",
  created_at: "2026-11-14T10:00:00", use_count: 0, last_used_at: null, superseded_by: null, meta: {}, source_label: "manual", related: [],
});

describe("Review", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("approves one memory and takes it off the list", async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      const body = url.startsWith("/api/memory/proposed") ? { items: [memory(5, "Alex likes short commits."), memory(6, "Run checks before deploying.")] }
        : url.startsWith("/api/memory/summary") ? summary : { ids: JSON.parse(String(init?.body)).ids, status: "active" };
      return new Response(JSON.stringify(body), { status: 200 });
    });
    vi.stubGlobal("fetch", fetch);
    render(<Review />);
    const card = (await screen.findByText("Alex likes short commits.")).closest("[data-slot=card]") as HTMLElement;
    await userEvent.click(within(card).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.queryByText("Alex likes short commits.")).not.toBeInTheDocument());
    const post = fetch.mock.calls.find(([url]) => url === "/api/memory/status")!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ ids: [5], status: "active" });
    expect(screen.getByText("Run checks before deploying.")).toBeInTheDocument();
  });
});
