// UIFPE-599: the pure half of adding a destination, shared by the Destinations
// screen and the wizard's dialog.
import { describe, expect, it } from "vitest";
import {
  EMPTY_FORM,
  canCreate,
  createActionLabel,
  createBody,
  createBusyLabel,
  createHint,
  needsTestBeforeUse,
  nextStepAfterCreate,
  readyMessage,
  splitRecipients,
} from "../destinationForm.js";

describe("createBody", () => {
  it("builds an s3 body with the prefix only", () => {
    const body = createBody({ ...EMPTY_FORM, name: "Drop", channel: "s3",
                              destination_s3_prefix: "s3://b/in", visibility: "public" });
    expect(body).toEqual({ name: "Drop", channel: "s3", domain: "product",
                           visibility: "public", destination_s3_prefix: "s3://b/in" });
  });

  it("splits email recipients and drops blanks", () => {
    const body = createBody({ ...EMPTY_FORM, name: "Digest", channel: "email",
                              recipients: " a@x.com , ,b@y.com" });
    expect(body.recipients).toEqual(["a@x.com", "b@y.com"]);
    expect(body).not.toHaveProperty("credential");
  });

  it("sends sftp credentials in the request and nowhere else", () => {
    const body = createBody({ ...EMPTY_FORM, name: "Ingram", channel: "sftp",
                              host: "sftp.x", remote_dir: "/in", username: "u", password: "p" });
    expect(body.host).toBe("sftp.x");
    expect(body.remote_dir).toBe("/in");
    expect(body.credential).toEqual({ host: "sftp.x", username: "u", password: "p" });
  });
});

describe("canCreate", () => {
  it("needs a name and the channel's own field", () => {
    expect(canCreate({ ...EMPTY_FORM, name: "x" })).toBe(false); // s3 with no prefix
    expect(canCreate({ ...EMPTY_FORM, name: "x", destination_s3_prefix: "s3://b" })).toBe(true);
    expect(canCreate({ ...EMPTY_FORM, name: "", destination_s3_prefix: "s3://b" })).toBe(false);
    expect(canCreate({ ...EMPTY_FORM, name: "x", channel: "email", recipients: " " })).toBe(false);
    expect(canCreate({ ...EMPTY_FORM, name: "x", channel: "email", recipients: "a@b.c" })).toBe(true);
    expect(canCreate({ ...EMPTY_FORM, name: "x", channel: "sftp", host: "h" })).toBe(false);
    expect(canCreate({ ...EMPTY_FORM, name: "x", channel: "sftp", host: "h", remote_dir: "/in" })).toBe(true);
  });
});

describe("needsTestBeforeUse", () => {
  it("email may be used at once; sftp and s3 are tested first", () => {
    expect(needsTestBeforeUse("email")).toBe(false);
    expect(needsTestBeforeUse("sftp")).toBe(true);
    expect(needsTestBeforeUse("s3")).toBe(true);
  });
});

describe("splitRecipients", () => {
  it("tolerates missing input", () => {
    expect(splitRecipients(undefined)).toEqual([]);
    expect(splitRecipients("")).toEqual([]);
  });
});

// UIFPE-633: the add flow's per-channel decisions, asked by BOTH the
// Destinations screen and the wizard's dialog so they cannot drift again
describe("createActionLabel", () => {
  it("tests SFTP and S3 first, in both places", () => {
    for (const context of ["screen", "export"]) {
      expect(createActionLabel("sftp", context)).toBe("Create & test");
      expect(createActionLabel("s3", context)).toBe("Create & test");
    }
  });

  it("email is created as it is — and chosen at once in the wizard", () => {
    expect(createActionLabel("email", "screen")).toBe("Create");
    expect(createActionLabel("email", "export")).toBe("Create & use");
    expect(createActionLabel("email")).toBe("Create"); // screen by default
  });
});

describe("createBusyLabel", () => {
  it("says which half is running", () => {
    expect(createBusyLabel("creating")).toBe("Creating…");
    expect(createBusyLabel("testing")).toBe("Testing connection…");
  });
});

describe("nextStepAfterCreate", () => {
  it("runs the test for SFTP and S3, nothing for email", () => {
    expect(nextStepAfterCreate({ channel: "sftp" })).toBe("test");
    expect(nextStepAfterCreate({ channel: "s3" })).toBe("test");
    expect(nextStepAfterCreate({ channel: "email" })).toBe("ready");
  });
});

// UIFPE-635: a partner's own bucket fails its test until they apply our
// bucket policy — the next step is sending it, not a test that must fail
describe("nextStepAfterCreate — external S3 bucket", () => {
  it("asks for the partner policy first", () => {
    expect(nextStepAfterCreate({ channel: "s3", partner_policy: { statement: {} } })).toBe("policy");
  });

  it("our own data bucket (no policy) is tested at once", () => {
    expect(nextStepAfterCreate({ channel: "s3", partner_policy: null })).toBe("test");
  });

  it("a policy never changes SFTP or email", () => {
    expect(nextStepAfterCreate({ channel: "sftp", partner_policy: {} })).toBe("test");
    expect(nextStepAfterCreate({ channel: "email", partner_policy: {} })).toBe("ready");
  });
});

describe("createHint", () => {
  it("tells the wizard author when the destination is chosen", () => {
    expect(createHint("sftp", "export")).toMatch(/test runs right after saving/);
    expect(createHint("email", "export")).toMatch(/as soon as it is saved/);
  });

  // UIFPE-636: nothing false for any channel
  it("Destinations screen: only SFTP mentions Secrets Manager", () => {
    expect(createHint("sftp", "screen")).toMatch(/Secrets Manager/);
    expect(createHint("s3", "screen")).not.toMatch(/Secrets Manager/);
    expect(createHint("email", "screen")).not.toMatch(/Secrets Manager/);
  });

  it("Destinations screen: email is usable at once, SFTP and S3 after their test", () => {
    expect(createHint("email", "screen")).toMatch(/at once/);
    expect(createHint("email", "screen")).not.toMatch(/must pass/);
    expect(createHint("sftp", "screen")).toMatch(/once it passes/);
    expect(createHint("s3", "screen")).toMatch(/bucket policy/);
  });
});

describe("readyMessage", () => {
  it("says the destination is usable now, with no credential claim", () => {
    const text = readyMessage({ name: "Ops digest", channel: "email" });
    expect(text).toMatch(/^Ops digest created — exports can use it now/);
    expect(text).not.toMatch(/Secrets Manager|pending/);
  });
});
