// Wiring layer — bridges the Claude Designer artifact to the real
// Subsonic API. Lives outside the artifact (underscored filename) so
// the next design-zip drop only replaces `data.jsx`, `app.jsx`,
// `chrome.jsx`, etc., and not the wiring code below.
//
// What this file does:
//
//   1. Wraps `MK_LoginView` so submitting the form calls
//      `MK_API.login()` instead of just setting authed=true. On
//      success we save the session, fetch the library, populate
//      `MK_DATA`, and only then call the original `onConnect` to flip
//      the App into the shell view.
//
//   2. Replaces `MK_makeCover()` with a server-cover-art version when
//      the input string is a real Subsonic cover-art id (the legacy
//      procedural one stays for placeholder cases — empty/null ids).
//
//   3. Hooks `MK_AUDIO` events into App's pos / dur state by
//      polling the artifact's state setters indirectly: the artifact
//      already calls `MK_AUDIO.play/pause/seek/...` from the patched
//      action handlers in `app.jsx`. The pos-and-duration push-back
//      lives there too; see the comments in `app.jsx`.

(function () {
  "use strict";

  // ---------------------------------------------------------------
  // 1. Patch MK_LoginView so onConnect runs a real auth + data load.
  // ---------------------------------------------------------------
  const OriginalLoginView = window.MK_LoginView;
  if (typeof OriginalLoginView !== "function") {
    console.error("[wiring] MK_LoginView missing — designer artifact load order is wrong");
    return;
  }

  function WiredLoginView(props) {
    async function handleConnect({ url, user, pass }) {
      // Authenticate against the real server, then preload the library
      // tree. Re-throw on failure so views.jsx's submit() catch sets
      // the LoginView's `err` state, which renders the .mk-login-error
      // message. Without re-throw the form silently looks like nothing
      // happened on connection-refused / wrong-credentials.
      try {
        const session = await window.MK_API.login({
          baseUrl: url,
          user,
          password: pass,
        });
        await loadLibrary(session);
        // Tell the artifact's App() to flip into the shell view.
        props.onConnect({ url: session.baseUrl, user: session.user, pass });
      } catch (err) {
        console.error("[wiring] login failed:", err);
        // Map raw fetch/network errors to something a human can act on.
        // Each browser emits a different message for "couldn't connect":
        //   Chromium: "Failed to fetch", "TypeError: NetworkError"
        //   WebKit (Safari / Tauri on macOS): "Load failed"
        //   Firefox: "NetworkError when attempting to fetch"
        // Plus DNS / TLS / timeout variants. Treat anything that smells
        // like a network-layer failure as connection-refused.
        const raw = String(err?.message || err);
        const networkSignals = /Failed to fetch|NetworkError|ERR_CONNECTION_REFUSED|Load failed|Could not connect|ENOTFOUND|timed? out/i;
        let friendly = raw;
        if (networkSignals.test(raw) || err instanceof TypeError) {
          friendly = `Couldn't reach the server at ${url}. Is it running and reachable from this device?`;
        } else if (/Subsonic 40/i.test(raw)) {
          friendly = "Wrong username or password.";
        } else if (/HTTP 401/i.test(raw)) {
          friendly = "Server rejected the credentials (HTTP 401).";
        } else if (/HTTP 5\d\d/i.test(raw)) {
          friendly = `Server error (${raw}). Check the serve logs.`;
        }
        throw new Error(friendly);
      }
    }
    return React.createElement(OriginalLoginView, {
      ...props,
      onConnect: handleConnect,
    });
  }
  window.MK_LoginView = WiredLoginView;

  // ---------------------------------------------------------------
  // 2. Cover-art override — return real <img>-friendly URLs when the
  //    album object has `coverArtUrl` set; fall back to the artifact's
  //    procedural generator otherwise.
  // ---------------------------------------------------------------
  const OriginalMakeCover = window.MK_makeCover;
  window.MK_makeCover = function wiredMakeCover(kind, baseColor) {
    // The artifact passes `al.cover` as the first arg. After the
    // adapter ran, that field IS the asset URL (string) for any album
    // that has cover art on the server. If it's still a procedural
    // tag (e.g. "enya", "blue") we fall through to the original.
    if (typeof kind === "string" && /^https?:\/\//.test(kind)) {
      return kind;
    }
    return OriginalMakeCover ? OriginalMakeCover(kind, baseColor) : "";
  };

  // ---------------------------------------------------------------
  // 3. Library loader — populate window.MK_DATA from the API.
  // ---------------------------------------------------------------
  //
  // The artifact's chrome reads `window.MK_DATA.ARTISTS` (and friends)
  // at every render. To support partial loads we mutate the same
  // MK_DATA in place — first with `getArtists` (a flat artist list,
  // arrives in ~1 round-trip), then with `getArtist`/`getAlbum`
  // details fired in parallel. A consumer who renders early will see
  // an artist list with empty `albums` arrays; once the per-artist
  // details land the arrays fill in. Same approach for stations
  // (also a single round-trip).
  //
  // Concurrency: we kick all `getArtist` calls in parallel (Promise.all)
  // rather than serialising them. The musickit serve bump-tested its
  // thread pool to 256 for exactly this; other Subsonic servers handle
  // it fine too. For libraries with hundreds of artists this turns a
  // multi-second waterfall into a single round-trip + the slowest
  // per-artist call.
  async function loadLibrary(session) {
    const api = window.MK_API;

    // Phase 1: roots — flat artist list + radio stations in parallel.
    const [artists, radio] = await Promise.all([
      api.getArtists(session),
      api.getInternetRadioStations(session).catch(() => []),
    ]);

    // Pre-populate ARTISTS with placeholder shells (empty albums) so a
    // consumer that renders right now sees the sidebar populated.
    const seed = artists.map((a) => ({
      id: a.id,
      name: a.name,
      sortName: a.name,
      albumCount: a.albumCount || 0,
      trackCount: 0,
      bio: "",
      color: "#444",
      cover: api.coverArtUrl(session, a.coverArt || a.id, 200),
      albums: [],
    }));
    seed.sort((x, y) => x.name.localeCompare(y.name));

    const stations = (radio || []).map((s) => ({
      id: s.id,
      name: s.name,
      streamUrl: s.streamUrl,
      homepageUrl: s.homepageUrl || "",
      icon: "(((",
    }));

    window.MK_DATA = {
      ARTISTS: seed,
      STATIONS: stations,
      LYRICS_BOADICEA: [],
    };
    window.MK_SESSION = session;

    // Phase 2: per-artist album + per-album track fetches, all in
    // parallel. Each artist's slot in `seed` gets filled in place so
    // any in-flight UI re-render picks it up.
    const slots = new Map(seed.map((s) => [s.id, s]));
    await Promise.all(
      artists.map(async (a) => {
        const slot = slots.get(a.id);
        if (!slot) return;
        let detail;
        try {
          detail = await api.getArtist(session, a.id);
        } catch (err) {
          console.warn("[wiring] getArtist failed for", a.id, err);
          return; // leave the slot's empty albums in place; UI shows skeleton
        }
        const albums = detail.album || [];
        const wiredAlbums = await Promise.all(
          albums.map(async (al) => {
            let full;
            try {
              full = await api.getAlbum(session, al.id);
            } catch (err) {
              console.warn("[wiring] getAlbum failed for", al.id, err);
              return null;
            }
            const tracks = (full.song || []).map((s) => ({
              n: s.track ?? 0,
              title: s.title || "",
              time: formatDuration(s.duration),
              starred: !!s.starred,
              trackId: s.id,
              artistId: a.id,
              albumId: al.id,
              artist: s.artist || a.name,
              suffix: s.suffix || "",
            }));
            return {
              id: al.id,
              name: al.name,
              year: al.year || "",
              trackCount: al.songCount || tracks.length,
              color: "#444",
              cover: api.coverArtUrl(session, al.coverArt || al.id, 200),
              coverArtUrl: api.coverArtUrl(session, al.coverArt || al.id, 600),
              tracks,
            };
          })
        );
        slot.albums = wiredAlbums.filter(Boolean);
        slot.trackCount = slot.albums.reduce((n, al) => n + (al.trackCount || 0), 0);
      })
    );
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return "00:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }

  // ---------------------------------------------------------------
  // 4. Auto-resume — if we have a stored session, populate MK_DATA
  //    before App() first mounts and skip the login form.
  // ---------------------------------------------------------------
  //
  // Only clear the persisted session on AUTH failures (Subsonic 40,
  // HTTP 401). A transient network error or a one-off bad-album
  // response shouldn't kick the user back to the login screen and
  // wipe their stored credentials — they'd lose the ability to retry
  // once their wifi comes back, even though the credentials are still
  // valid. We re-throw non-auth errors to the caller so the UI can
  // decide whether to show a banner or retry.
  function isAuthError(err) {
    const msg = String(err?.message || err);
    return /Subsonic 40\b/i.test(msg) || /HTTP 401/i.test(msg);
  }

  // Hard ceiling on how long MK_RESUME can block before the splash
  // gives up and falls through to the shell anyway. The splash overlay
  // covers the whole window, so a hang here = a fully-black UI the
  // user can't escape. Real network + library load (~52 artists, 250
  // tracks) on a LAN takes under 2 seconds; a 12-second cap is well
  // beyond that but still under the user's "what is happening?"
  // threshold. The library populates in the background after that.
  const RESUME_SOFT_TIMEOUT_MS = 12000;

  window.MK_RESUME = async function resume() {
    const session = window.MK_API.loadSession();
    if (!session) return null;
    try {
      await Promise.race([
        loadLibrary(session),
        new Promise((_, rej) =>
          setTimeout(() => rej(new Error("resume timeout")), RESUME_SOFT_TIMEOUT_MS)
        ),
      ]);
      return session;
    } catch (err) {
      if (isAuthError(err)) {
        console.warn("[wiring] resume: auth failed, clearing session:", err);
        window.MK_API.clearSession();
        return null;
      }
      console.warn("[wiring] resume: transient failure or timeout, keeping session:", err);
      // Keep the session and the (possibly partial) data so the shell
      // can render with what we have. A retry from the UI will
      // re-trigger loadLibrary fresh.
      return session;
    }
  };
})();
