// Subsonic API client — generic, talks to any spec-compliant server
// (musickit serve, Navidrome, Airsonic, Gonic, etc.).
//
// Auth model: salted-token per the Subsonic spec. The login page
// accepts a raw password, computes `token = md5(password + salt)` once,
// and from that point on we send `?u=&t=&s=` on every request — the
// raw password never crosses the wire again.
//
// This module exposes:
//   - subsonicClient(host, user, token, salt) — creates a client bound
//     to one server's credentials
//   - tryLogin({host, user, password}) — full login flow: probes ping
//     with new salt+token, returns a client on success
//
// Every request goes through `query()` which:
//   1. Builds the URL with auth params + json format + UA
//   2. fetch()s it
//   3. Parses the subsonic-response envelope
//   4. Returns the inner payload OR throws a SubsonicError

import { md5 } from "./md5.js";

const SUBSONIC_VERSION = "1.16.1";
const CLIENT_NAME = "musickit-desktop";

// Subsonic spec error codes worth handling explicitly.
const ERROR = {
  WRONG_AUTH: 40,
  TOKEN_AUTH_NOT_SUPPORTED: 41,
  USER_NOT_AUTHORIZED: 50,
  TRIAL_OVER: 60,
  NOT_FOUND: 70,
};

export class SubsonicError extends Error {
  constructor(code, message) {
    super(`subsonic error ${code}: ${message}`);
    this.code = code;
    this.subsonicMessage = message;
  }
}

/** Generate a fresh salt for token computation. */
export function makeSalt() {
  // 16 hex chars (~64 bits) — plenty of entropy and small over the wire.
  // randomUUID is universally available in modern webviews.
  return crypto.randomUUID().replace(/-/g, "").slice(0, 16);
}

/** `token = md5(password + salt)` per the Subsonic spec. */
export function makeToken(password, salt) {
  return md5(password + salt);
}

/** Return a client bound to {host, user, token, salt}. */
export function subsonicClient({ host, user, token, salt }) {
  // `host` may have a trailing slash; normalise.
  const base = host.replace(/\/+$/, "");
  return {
    host: base,
    user,
    token,
    salt,

    /** Build a fully-qualified URL for an endpoint with extra params. */
    url(endpoint, params = {}) {
      const u = new URL(`${base}/rest/${endpoint}`);
      u.searchParams.set("u", user);
      u.searchParams.set("t", token);
      u.searchParams.set("s", salt);
      u.searchParams.set("v", SUBSONIC_VERSION);
      u.searchParams.set("c", CLIENT_NAME);
      u.searchParams.set("f", "json");
      for (const [k, v] of Object.entries(params)) {
        if (v === undefined || v === null) continue;
        u.searchParams.set(k, String(v));
      }
      return u.toString();
    },

    /** URL with `f=raw` for media endpoints (stream / download / cover). */
    mediaUrl(endpoint, params = {}) {
      // Same auth params, but no `f=json` — these endpoints return bytes.
      const u = new URL(`${base}/rest/${endpoint}`);
      u.searchParams.set("u", user);
      u.searchParams.set("t", token);
      u.searchParams.set("s", salt);
      u.searchParams.set("v", SUBSONIC_VERSION);
      u.searchParams.set("c", CLIENT_NAME);
      for (const [k, v] of Object.entries(params)) {
        if (v === undefined || v === null) continue;
        u.searchParams.set(k, String(v));
      }
      return u.toString();
    },

    /** Fetch + envelope-unwrap a JSON endpoint. */
    async query(endpoint, params = {}) {
      const url = this.url(endpoint, params);
      let resp;
      try {
        resp = await fetch(url);
      } catch (e) {
        throw new Error(`network error reaching ${base}: ${e?.message || e}`);
      }
      if (!resp.ok) {
        throw new Error(`http ${resp.status} from ${base}`);
      }
      const body = await resp.json();
      const inner = body?.["subsonic-response"];
      if (!inner) {
        throw new Error(`response from ${base} is not a Subsonic envelope`);
      }
      if (inner.status !== "ok") {
        const err = inner.error || {};
        throw new SubsonicError(err.code ?? -1, err.message ?? "unknown error");
      }
      return inner;
    },
  };
}

/**
 * Attempt to log in. Returns a {client, serverInfo} on success or
 * throws on failure. `serverInfo` carries the server's reported type
 * + version (useful for the UI to label "MusicKit", "Navidrome", etc.).
 */
export async function tryLogin({ host, user, password }) {
  const salt = makeSalt();
  const token = makeToken(password, salt);
  const client = subsonicClient({ host, user, token, salt });
  const inner = await client.query("ping");
  return {
    client,
    serverInfo: {
      type: inner.type ?? "subsonic",
      serverVersion: inner.serverVersion ?? null,
      apiVersion: inner.version ?? null,
      openSubsonic: !!inner.openSubsonic,
    },
  };
}

export const SUBSONIC_ERRORS = ERROR;
