// Subsonic API client — talks to any spec-compliant `/rest/*` server
// (musickit serve, Navidrome, Airsonic, Gonic, ...).
//
// Auth model: salted-token per request. The user submits password ONCE
// at the login screen; from then on we compute `t = md5(password + s)`
// with a fresh random salt for every request, so the plaintext
// password never crosses the wire and never persists. Token + salt do
// persist (in localStorage) so refreshing the page keeps the session.
//
// Lives outside the Claude Designer artifact (note the leading
// underscore) so future zip drops only replace src/*.jsx + musickit.css
// without touching the wiring layer. The artifact reaches us through
// `window.MK_API`.

(function () {
  "use strict";

  const STORAGE_KEY = "musickit.design.session";

  const CLIENT_NAME = "musickit-design";
  const API_VERSION = "1.16.1";

  function genSalt() {
    const bytes = new Uint8Array(12);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }

  function buildAuthQuery(session) {
    const salt = genSalt();
    const token = window.MK_md5(session.password + salt);
    const qs = new URLSearchParams({
      u: session.user,
      t: token,
      s: salt,
      v: API_VERSION,
      c: CLIENT_NAME,
      f: "json",
    });
    return qs;
  }

  async function call(session, endpoint, params) {
    const qs = buildAuthQuery(session);
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        if (v !== undefined && v !== null) qs.set(k, String(v));
      }
    }
    const url = `${session.baseUrl}/rest/${endpoint}?${qs.toString()}`;
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`HTTP ${response.status} on ${endpoint}`);
    }
    const json = await response.json();
    const inner = json["subsonic-response"];
    if (!inner) throw new Error(`bad envelope on ${endpoint}`);
    if (inner.status === "failed") {
      const code = inner.error?.code ?? "?";
      const msg = inner.error?.message ?? "unknown error";
      throw new Error(`Subsonic ${code}: ${msg}`);
    }
    return inner;
  }

  // Build a URL for cover art / streams. These get pasted into <img>
  // and <audio> elements directly, so they need the auth query string
  // embedded. A fresh salt+token per URL is fine — the server doesn't
  // care that consecutive requests rotate.
  function makeAssetUrl(session, endpoint, id, extra) {
    const qs = buildAuthQuery(session);
    qs.set("id", id);
    if (extra) for (const [k, v] of Object.entries(extra)) qs.set(k, String(v));
    return `${session.baseUrl}/rest/${endpoint}?${qs.toString()}`;
  }

  // Session lifecycle ----------------------------------------------------

  function loadSession() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      const s = JSON.parse(raw);
      if (!s.baseUrl || !s.user || !s.password) return null;
      return s;
    } catch {
      return null;
    }
  }

  function saveSession(session) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(session));
  }

  function clearSession() {
    localStorage.removeItem(STORAGE_KEY);
  }

  // Public API surface ---------------------------------------------------
  // Every method takes a `session` object: { baseUrl, user, password }.
  // Errors throw — callers should catch and surface to the UI.

  const MK_API = {
    loadSession,
    saveSession,
    clearSession,

    async login({ baseUrl, user, password }) {
      const base = baseUrl.replace(/\/+$/, "");
      const session = { baseUrl: base, user, password };
      // Pings throws if creds bad, so login itself is the validation.
      await call(session, "ping");
      saveSession(session);
      return session;
    },

    async getArtists(session) {
      const r = await call(session, "getArtists");
      const index = r.artists?.index ?? [];
      const out = [];
      for (const group of index) {
        for (const a of group.artist || []) out.push(a);
      }
      return out;
    },

    async getArtist(session, id) {
      const r = await call(session, "getArtist", { id });
      return r.artist;
    },

    async getAlbum(session, id) {
      const r = await call(session, "getAlbum", { id });
      return r.album;
    },

    async getInternetRadioStations(session) {
      const r = await call(session, "getInternetRadioStations");
      return r.internetRadioStations?.internetRadioStation ?? [];
    },

    async getStarred2(session) {
      const r = await call(session, "getStarred2");
      return r.starred2 ?? { artist: [], album: [], song: [] };
    },

    async search3(session, query, opts) {
      const r = await call(session, "search3", {
        query,
        artistCount: opts?.artistCount ?? 20,
        albumCount: opts?.albumCount ?? 20,
        songCount: opts?.songCount ?? 40,
      });
      return r.searchResult3 ?? { artist: [], album: [], song: [] };
    },

    async star(session, { id, albumId, artistId }) {
      await call(session, "star", { id, albumId, artistId });
    },

    async unstar(session, { id, albumId, artistId }) {
      await call(session, "unstar", { id, albumId, artistId });
    },

    coverArtUrl(session, coverArtId, size) {
      if (!coverArtId) return null;
      return makeAssetUrl(session, "getCoverArt", coverArtId, size ? { size } : null);
    },

    streamUrl(session, trackId) {
      return makeAssetUrl(session, "stream", trackId);
    },
  };

  window.MK_API = MK_API;
})();
