// Query preview client (UIFPE-219). currentArtifacts reads the REAL repo via
// build-time globs — pinned against the registered product entity so a moved
// artifact breaks here, not silently in the Review step.
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  compilePreviewConfigured, currentArtifacts, fetchCompilePreview,
  COMPILE_PREVIEW_URL,
} from "../compilePreview";

describe("currentArtifacts", () => {
  it("finds all four registered product artifacts + the pipeline config", () => {
    const a = currentArtifacts("product", "gold_product.product");
    expect(a).not.toBeNull();
    expect(a!.mapping).toContain('"goldColumnName"');
    expect(a!.master).toContain('"properties"');
    expect(a!.manifest).toContain("silver_table:");
    expect(a!.sql).toContain("FROM items");
    expect(a!.pipeline).toContain("frequency:");
  });

  it("serves LF-only texts whatever the checkout's line endings", () => {
    // the service normalizes to LF when it reads the texts back — a CRLF
    // "before" would make every line differ by an invisible \r and the
    // Review diff render a whole-file replace (found live, 2026-09-02)
    const a = currentArtifacts("product", "gold_product.product")!;
    for (const [k, text] of Object.entries(a))
      expect(text.includes("\r"), `artifact ${k} carries \\r`).toBe(false);
  });

  it("returns null when the entity is not registered (a new table)", () => {
    expect(currentArtifacts("product", "gold_product.brand_new")).toBeNull();
    expect(currentArtifacts("nosuch", "gold_x.x")).toBeNull();
    expect(currentArtifacts(null, null)).toBeNull();
  });
});

describe("fetchCompilePreview", () => {
  afterEach(() => vi.unstubAllGlobals());
  const arts = currentArtifacts("product", "gold_product.product")!;

  it("treats the deploy-injected service URL consistently", () => {
    // COMPILE_PREVIEW_URL comes from VITE_COMPILE_PREVIEW_URL, which only the
    // deploy sets (Terraform's compile_preview_url output, first printed
    // 2026-09-02). So it is EMPTY in a local or CI build -- and this test used
    // to assert the URL was well-formed AND that configured() was true, which
    // no build without the variable can satisfy. It contradicted its own
    // comment and could only ever pass on a deployed bundle; nothing ran it to
    // notice (UIFPE-738).
    //
    // What is checkable anywhere is the CONTRACT: configured() tracks whether
    // the URL is set, and when set the URL is a Lambda function URL. The
    // deployed value itself is the deploy's business, not CI's.
    if (COMPILE_PREVIEW_URL === "") {
      expect(compilePreviewConfigured()).toBe(false);
    } else {
      expect(COMPILE_PREVIEW_URL).toMatch(/^https:\/\/.+lambda-url.+on\.aws\/$/);
      expect(compilePreviewConfigured()).toBe(true);
    }
  });

  it("posts envelope + artifacts and returns the files map", async () => {
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      void init;
      return new Response(JSON.stringify({
        intent: "add", log: ["[AUTO] x: done"],
        files: { sql: { changed: true, after: "SELECT 1" } },
      }), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const r = await fetchCompilePreview({ intake: {} }, arts);
    expect(r).toMatchObject({
      ok: true, log: ["[AUTO] x: done"],
      files: { sql: { changed: true, after: "SELECT 1" } },
    });
    const sent = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(sent.envelope).toEqual({ intake: {} });
    expect(sent.artifacts.sql).toBe(arts.sql);
  });

  it("carries the compiler's refusal through as a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      error: "derived column refused (ADR-0008 battery)", refusal: true,
    }), { status: 400 })));
    const r = await fetchCompilePreview({}, arts);
    expect(r).toEqual({ ok: false, refusal: true,
                        error: "derived column refused (ADR-0008 battery)" });
  });

  it("a network failure is unavailable, never a refusal", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new Error("Failed to fetch");
    }));
    const r = await fetchCompilePreview({}, arts);
    expect(r).toEqual({ ok: false, refusal: false, error: "Failed to fetch" });
  });
});
