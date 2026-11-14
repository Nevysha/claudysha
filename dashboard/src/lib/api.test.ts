import { api, query, readToken } from "./api";

describe("readToken", () => {
  it("takes the token from the link, keeps the route and hides the token", () => {
    history.replaceState(null, "", "/#/review&token=abc-123");
    expect(readToken()).toBe("abc-123");
    expect(location.hash).toBe("#/review");
    expect(sessionStorage.getItem("claudysha-token")).toBe("abc-123");
  });
});

describe("api", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the token, and JSON when posting", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: 1 }), { status: 200 }));
    vi.stubGlobal("fetch", fetch);
    expect(await api("/api/x", { a: 1 })).toEqual({ ok: 1 });
    const [, init] = fetch.mock.calls[0]!;
    expect(init.method).toBe("POST");
    expect(init.headers["X-Claudysha-Token"]).toBe(readToken());
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("throws the server's message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "no such memory" }), { status: 400 })));
    await expect(api("/api/x")).rejects.toThrow("no such memory");
  });
});

it("query leaves out empty values", () => {
  expect(query({ q: "", scope: "a b", unused: false, n: 0, x: undefined })).toBe("?scope=a+b&n=0");
});
