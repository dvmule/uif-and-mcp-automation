// UIFPE-539: the picker's pure logic — filtering, paging, threshold.
import { describe, expect, it } from "vitest";
import {
  PICKER_PAGE_SIZE,
  PICKER_THRESHOLD,
  channelLabel,
  channelsOf,
  destinationAddress,
  filterDestinationRows,
  destinationMatches,
  filterDestinations,
  isVerified,
  pageBounds,
  selectableWhileUntested,
  statusText,
  testActionLabel,
} from "../destinationPicker.js";

const dest = (over = {}) => ({
  id: "d1",
  name: "Ingram Content Group",
  channel: "sftp",
  status: "verified",
  ...over,
});

describe("isVerified", () => {
  it("reads pending-* as unverified, anything else as verified", () => {
    expect(isVerified(dest({ status: "pending_test" }))).toBe(false);
    expect(isVerified(dest({ status: "pending" }))).toBe(false);
    expect(isVerified(dest({ status: "verified" }))).toBe(true);
    expect(isVerified(dest({ status: undefined }))).toBe(true);
  });
});

describe("destinationMatches", () => {
  it("matches by name, case-insensitively", () => {
    expect(destinationMatches(dest(), { query: "ingram" })).toBe(true);
    expect(destinationMatches(dest(), { query: "INGRAM" })).toBe(true);
    expect(destinationMatches(dest(), { query: "amazon" })).toBe(false);
  });

  it("matches the rendered target too — authors remember hosts and buckets", () => {
    expect(
      destinationMatches(dest(), { query: "ingramcontent.example" }, "sftp.ingramcontent.example:22/inbound")
    ).toBe(true);
  });

  it("filters by channel", () => {
    expect(destinationMatches(dest(), { channel: "sftp" })).toBe(true);
    expect(destinationMatches(dest(), { channel: "s3" })).toBe(false);
    expect(destinationMatches(dest(), { channel: "" })).toBe(true);
  });

  it("verifiedOnly hides pending destinations", () => {
    const pending = dest({ status: "pending_test" });
    expect(destinationMatches(pending, { verifiedOnly: true })).toBe(false);
    expect(destinationMatches(pending, { verifiedOnly: false })).toBe(true);
  });

  it("an empty toolbar matches everything", () => {
    expect(destinationMatches(dest(), {})).toBe(true);
  });
});

describe("filterDestinations", () => {
  const list = [
    dest({ id: "a", name: "Amazon drop", channel: "s3" }),
    dest({ id: "b", name: "Ingram SFTP", channel: "sftp" }),
    dest({ id: "c", name: "Trade Ops digest", channel: "email", status: "pending_test" }),
  ];

  it("combines query, channel and verified filters", () => {
    expect(filterDestinations(list, {}).map((d) => d.id)).toEqual(["a", "b", "c"]);
    expect(filterDestinations(list, { channel: "email" }).map((d) => d.id)).toEqual(["c"]);
    expect(filterDestinations(list, { verifiedOnly: true }).map((d) => d.id)).toEqual(["a", "b"]);
    expect(filterDestinations(list, { query: "ingram" }).map((d) => d.id)).toEqual(["b"]);
  });

  it("tolerates a missing list", () => {
    expect(filterDestinations(undefined, {})).toEqual([]);
  });
});

describe("pageBounds", () => {
  it("slices a page out of the filtered total", () => {
    expect(pageBounds(20, 0, 8)).toEqual({ page: 0, pages: 3, start: 0, end: 8 });
    expect(pageBounds(20, 2, 8)).toEqual({ page: 2, pages: 3, start: 16, end: 20 });
  });

  it("clamps the page when a filter shrinks the list", () => {
    expect(pageBounds(5, 4, 8)).toEqual({ page: 0, pages: 1, start: 0, end: 5 });
  });

  it("an empty result still has one (empty) page", () => {
    expect(pageBounds(0, 0, 8)).toEqual({ page: 0, pages: 1, start: 0, end: 0 });
  });

  it("defaults to the picker page size", () => {
    expect(pageBounds(PICKER_PAGE_SIZE + 1, 0).pages).toBe(2);
  });
});

describe("channelsOf", () => {
  it("returns the distinct channels present, sorted", () => {
    const list = [dest({ channel: "sftp" }), dest({ channel: "s3" }), dest({ channel: "sftp" })];
    expect(channelsOf(list)).toEqual(["s3", "sftp"]);
    expect(channelsOf([])).toEqual([]);
  });
});

describe("PICKER_THRESHOLD", () => {
  it("keeps the card grid for small teams — up to ten (UIFPE-600)", () => {
    expect(PICKER_THRESHOLD).toBe(10);
  });
});

// UIFPE-595: an email destination showed "PENDING_CONNECTION_TEST" — a label
// about connecting to a server, for a channel with nothing to connect to —
// and there was no button anywhere that could clear it.
describe("statusText", () => {
  it("says what each channel is waiting for", () => {
    expect(statusText(dest({ channel: "email", status: "pending_connection_test" })))
      .toBe("Not tested yet — send a test email");
    expect(statusText(dest({ channel: "sftp", status: "pending_connection_test" })))
      .toBe("Not tested yet — connection not proven");
    expect(statusText(dest({ channel: "s3", status: "pending_connection_test" })))
      .toBe("Not tested yet — write access not proven");
  });

  it("a verified destination just reads verified", () => {
    expect(statusText(dest({ channel: "email", status: "verified" }))).toBe("verified");
  });

  it("an unknown channel still shows something true", () => {
    // a channel added later must not render blank: fall back to the status
    expect(statusText(dest({ channel: "webhook", status: "pending_connection_test" })))
      .toBe("pending connection test");
  });

  it("tolerates a missing destination", () => {
    expect(statusText(undefined)).toBe("");
  });
});

describe("testActionLabel", () => {
  it("names what the button actually does", () => {
    expect(testActionLabel("email")).toBe("Send test email");
    expect(testActionLabel("sftp")).toBe("Test");
    expect(testActionLabel("s3")).toBe("Test");
  });
});

describe("selectableWhileUntested", () => {
  it("lets an unproven EMAIL address be chosen", () => {
    // the failure lands in front of the author, not in a run that dies at
    // delivery — and nothing gates delivery on the status
    expect(selectableWhileUntested(dest({ channel: "email" }))).toBe(true);
  });

  it("keeps unproven sftp and s3 targets locked", () => {
    expect(selectableWhileUntested(dest({ channel: "sftp" }))).toBe(false);
    expect(selectableWhileUntested(dest({ channel: "s3" }))).toBe(false);
  });

  it("tolerates a missing destination", () => {
    expect(selectableWhileUntested(undefined)).toBe(false);
  });
});

// UIFPE-699: the Destinations screen's labels and list rules
describe("channelLabel / destinationAddress / filterDestinationRows", () => {
  const rows = [
    { name: "Ingram", channel: "sftp", host: "sftp.ingram.example", remote_dir: "/inbound" },
    { name: "Partner drop", channel: "s3", destination_s3_prefix: "s3://partner/in" },
    { name: "Ops mail", channel: "email", recipients: ["ops@example.com", "b@example.com"] },
  ];
  it("names S3 as AWS S3 and capitalises an unknown channel", () => {
    expect(channelLabel("s3")).toBe("AWS S3");
    expect(channelLabel("email")).toBe("Email");
    expect(channelLabel("ftp")).toBe("FTP");
  });
  it("shows the address each channel delivers to", () => {
    expect(rows.map(destinationAddress)).toEqual(["sftp.ingram.example:/inbound", "s3://partner/in", "ops@example.com, b@example.com"]);
  });
  it("hides switched-off channels and finds by name or address", () => {
    expect(filterDestinationRows(rows, { channelsOff: ["s3"] }).map((d) => d.name)).toEqual(["Ingram", "Ops mail"]);
    expect(filterDestinationRows(rows, { query: "ops@" }).map((d) => d.name)).toEqual(["Ops mail"]);
    expect(filterDestinationRows(rows, { query: "INGRAM" })).toHaveLength(1);
  });
});
