import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { lookupUpdate, parseVersion } from "../src/updateCheck.ts";

const ZIP = "https://github.com/indyjamesb/decky-plex-audiobooks/releases/download/v0.5.4/plex-audiobooks-v0.5.4.zip";
const SHA = "a".repeat(64);

function release(overrides: Record<string, unknown> = {}) {
  return {
    tag_name: "v0.5.4",
    assets: [
      {
        name: "plex-audiobooks-v0.5.4.zip",
        browser_download_url: ZIP,
        digest: "sha256:" + SHA,
      },
    ],
    ...overrides,
  };
}

describe("lookupUpdate", () => {
  it("accepts a newer release zip and strips the sha256 prefix", () => {
    const result = lookupUpdate(release(), "0.5.3");
    assert.equal(result.kind, "update");
    if (result.kind !== "update") return;
    assert.equal(result.offer.version, "v0.5.4");
    assert.equal(result.offer.url, ZIP);
    assert.equal(result.offer.sha256, SHA);
    assert.equal(result.offer.sha256.startsWith("sha256:"), false);
  });

  it("ignores the same version and older tags", () => {
    assert.equal(lookupUpdate(release(), "0.5.4").kind, "current");
    assert.equal(lookupUpdate(release({ tag_name: "v0.5.3" }), "0.5.4").kind, "current");
    assert.equal(lookupUpdate(release({ tag_name: "v0.5.2" }), "v0.5.3").kind, "current");
  });

  it("rejects a newer release whose zip is not from this repo", () => {
    const forged = release({
      assets: [
        {
          name: "plex-audiobooks-v0.5.4.zip",
          browser_download_url: "https://example.com/plex-audiobooks-v0.5.4.zip",
          digest: "sha256:" + SHA,
        },
      ],
    });
    assert.equal(lookupUpdate(forged, "0.5.3").kind, "unavailable");
  });

  it("rejects query strings, other digests, and the wrong file name", () => {
    assert.equal(lookupUpdate(release({
      assets: [{
        name: "plex-audiobooks-v0.5.4.zip",
        browser_download_url: ZIP + "?evil=1",
        digest: "sha256:" + SHA,
      }],
    }), "0.5.3").kind, "unavailable");
    assert.equal(lookupUpdate(release({
      assets: [{
        name: "plex-audiobooks-v0.5.4.zip",
        browser_download_url: ZIP,
        digest: "sha512:" + SHA,
      }],
    }), "0.5.3").kind, "unavailable");
    assert.equal(lookupUpdate(release({
      assets: [{
        name: "other.zip",
        browser_download_url: ZIP.replace("plex-audiobooks-v0.5.4.zip", "other.zip"),
        digest: "sha256:" + SHA,
      }],
    }), "0.5.3").kind, "unavailable");
  });

  it("parses a v-prefixed version", () => {
    assert.deepEqual(parseVersion("v0.5.3"), [0, 5, 3]);
    assert.equal(parseVersion("0.5"), null);
  });
});
