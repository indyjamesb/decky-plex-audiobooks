/** GitHub release check. One request per call; nothing here polls. */

let checkedOnLoad = false;

/** True once per frontend session, so opening the menu does not poll GitHub. */
export function claimLoadCheck(): boolean {
  if (checkedOnLoad) return false;
  checkedOnLoad = true;
  return true;
}

export const RELEASE_API =
  "https://api.github.com/repos/indyjamesb/decky-plex-audiobooks/releases/latest";

const DOWNLOAD_PREFIX = "https://github.com/indyjamesb/decky-plex-audiobooks/releases/download/";

export const PLUGIN_NAME = "Plex Audiobooks";

/** Decky InstallType.UPDATE. INSTALL is 0, REINSTALL is 1. */
export const INSTALL_TYPE_UPDATE = 2;

const RELEASES_LATEST = "https://github.com/indyjamesb/decky-plex-audiobooks/releases/latest";

export const ZIP_FALLBACK =
  "Decky can't install this from the plugin. Download the zip from " +
  RELEASES_LATEST +
  " and use Developer, Third-Party Plugins, Install Plugin from ZIP File, Browse.";

export interface UpdateOffer {
  version: string;
  url: string;
  sha256: string;
}

export type UpdateLookup =
  | { kind: "current" }
  | { kind: "unavailable"; reason: string }
  | { kind: "update"; offer: UpdateOffer };

interface ReleaseAsset {
  name?: string;
  browser_download_url?: string;
  digest?: string;
}

interface ReleasePayload {
  tag_name?: string;
  assets?: ReleaseAsset[];
}

export function parseVersion(text: string): [number, number, number] | null {
  const match = /^v?(\d+)\.(\d+)\.(\d+)$/.exec(String(text || "").trim());
  if (!match) return null;
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

export function isNewer(latest: string, installed: string): boolean {
  const next = parseVersion(latest);
  const current = parseVersion(installed);
  if (!next || !current) return false;
  for (let index = 0; index < 3; index += 1) {
    if (next[index] !== current[index]) return next[index] > current[index];
  }
  return false;
}

function assetUrlIsTrusted(url: string, fileName: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:" || parsed.hostname !== "github.com") return false;
  if (parsed.username || parsed.password || parsed.search || parsed.hash) return false;
  if (!url.startsWith(DOWNLOAD_PREFIX)) return false;
  if (parsed.pathname.includes("..")) return false;
  return parsed.pathname.endsWith("/" + fileName);
}

function sha256Digest(digest: string): string {
  const text = String(digest || "").trim().toLowerCase();
  if (!text.startsWith("sha256:")) return "";
  const hex = text.slice("sha256:".length);
  return /^[0-9a-f]{64}$/.test(hex) ? hex : "";
}

export function lookupUpdate(release: ReleasePayload, installed: string): UpdateLookup {
  if (!parseVersion(installed)) {
    return { kind: "unavailable", reason: "Couldn't read the installed version." };
  }
  const tag = String(release?.tag_name || "");
  if (!parseVersion(tag)) {
    return { kind: "unavailable", reason: "The latest release has no version." };
  }
  if (!isNewer(tag, installed)) return { kind: "current" };
  const numbers = parseVersion(tag);
  const fileName = `plex-audiobooks-v${numbers?.join(".")}.zip`;
  const assets = Array.isArray(release?.assets) ? release.assets : [];
  const asset = assets.find((item) => item?.name === fileName);
  const url = String(asset?.browser_download_url || "");
  const sha256 = sha256Digest(String(asset?.digest || ""));
  if (!asset || !assetUrlIsTrusted(url, fileName) || !sha256) {
    return {
      kind: "unavailable",
      reason:
        "A newer release is missing a verified zip. " + ZIP_FALLBACK,
    };
  }
  return { kind: "update", offer: { version: tag, url, sha256 } };
}

export async function fetchLatestRelease(): Promise<ReleasePayload> {
  const response = await fetch(RELEASE_API, {
    headers: { Accept: "application/vnd.github+json" },
  });
  if (!response.ok) throw new Error("GitHub returned " + response.status);
  return (await response.json()) as ReleasePayload;
}

type DeckyCall = (route: string, ...args: unknown[]) => Promise<unknown>;

export function deckyInstallCall(): DeckyCall | null {
  const backend = (window as unknown as { DeckyBackend?: { call?: DeckyCall } }).DeckyBackend;
  if (!backend || typeof backend.call !== "function") return null;
  return backend.call.bind(backend);
}

export async function installUpdate(offer: UpdateOffer): Promise<void> {
  const call = deckyInstallCall();
  if (!call) throw new Error("DeckyBackend.call is not available");
  const check = lookupUpdate(
    {
      tag_name: offer.version,
      assets: [
        {
          name: `plex-audiobooks-v${parseVersion(offer.version)?.join(".")}.zip`,
          browser_download_url: offer.url,
          digest: "sha256:" + offer.sha256,
        },
      ],
    },
    "0.0.0",
  );
  if (check.kind !== "update" || check.offer.url !== offer.url || check.offer.sha256 !== offer.sha256) {
    throw new Error("Refusing an update that is not a verified release zip");
  }
  await call(
    "utilities/install_plugin",
    offer.url,
    PLUGIN_NAME,
    offer.version,
    offer.sha256,
    INSTALL_TYPE_UPDATE,
  );
}
