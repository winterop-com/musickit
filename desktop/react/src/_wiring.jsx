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
    const [error, setError] = React.useState(null);
    const [busy, setBusy] = React.useState(false);

    async function handleConnect({ url, user, pass }) {
      setError(null);
      setBusy(true);
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
        setError(err.message || String(err));
      } finally {
        setBusy(false);
      }
    }

    // The artifact's LoginView prop contract is `onConnect({url, user})`.
    // We accept an extra `pass` field by wrapping the underlying form;
    // see the patched app.jsx for how the form passes it through.
    return React.createElement(
      "div",
      { className: "mk-login-wrap" },
      React.createElement(OriginalLoginView, {
        ...props,
        onConnect: handleConnect,
        busy,
        error,
      })
    );
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
  async function loadLibrary(session) {
    const api = window.MK_API;

    // getArtists is cheap; pull it first so the sidebar renders fast.
    const artists = await api.getArtists(session);

    // For each artist, fetch the album list. Then for each album, the
    // track list. This is O(artists + albums) round-trips — fine on a
    // small library, slow on a big one. A lazy-on-click variant lives
    // in TODO once we know the design holds.
    const flatArtists = [];
    for (const a of artists) {
      const detail = await api.getArtist(session, a.id);
      const albums = detail.album || [];
      const wiredAlbums = await Promise.all(
        albums.map(async (al) => {
          const full = await api.getAlbum(session, al.id);
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
      flatArtists.push({
        id: a.id,
        name: a.name,
        sortName: a.name,
        albumCount: a.albumCount || wiredAlbums.length,
        trackCount: wiredAlbums.reduce((n, al) => n + (al.trackCount || 0), 0),
        bio: "",
        color: "#444",
        cover: api.coverArtUrl(session, a.coverArt || a.id, 200),
        albums: wiredAlbums,
      });
    }
    flatArtists.sort((x, y) => x.name.localeCompare(y.name));

    const radio = await api.getInternetRadioStations(session);
    const stations = radio.map((s) => ({
      id: s.id,
      name: s.name,
      streamUrl: s.streamUrl,
      homepageUrl: s.homepageUrl || "",
      icon: "(((",
    }));

    window.MK_DATA = {
      ARTISTS: flatArtists,
      STATIONS: stations,
      LYRICS_BOADICEA: [],
    };
    // Stash the active session on window so app.jsx patches can read
    // it without us having to thread a prop through the artifact tree.
    window.MK_SESSION = session;
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
  window.MK_RESUME = async function resume() {
    const session = window.MK_API.loadSession();
    if (!session) return null;
    try {
      await loadLibrary(session);
      return session;
    } catch (err) {
      console.warn("[wiring] resume failed, clearing session:", err);
      window.MK_API.clearSession();
      return null;
    }
  };
})();
