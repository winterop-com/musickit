// MusicKit main app — three-pane browse, now-playing, transport, overlays.

const { useState: uS, useEffect: uE, useMemo: uM, useRef: uR, useCallback: uC } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "layout": "topband",
  "preset": "tokyo",
  "lcd": false,
  "lcdColor": "green",
  "theme": "dark",
  "accent": "tokyo",
  "viz": "bars",
  "density": "comfortable",
  "fontScale": 1,
  "showSpectrum": true,
  "fullbleedCover": false
}/*EDITMODE-END*/;

// Layout variants:
//   topband   — current MusicKit: Now Playing as top hero band (with Spectrum)
//   bottombar — slim bottom transport bar + expandable mini-player
//   rightrail — Spotify-ish dedicated right column for Now Playing

// Accent palettes. Each defines: accent (chrome links/active), highlight
// (now-playing track titles), viz [lo,mid,hi] gradient stops.
const PALETTES = {
  tokyo: {
    name: "Tokyo Night",
    accent: "#7aa2f7",
    highlight: "#ff9e64",
    viz: { lo: "#bcd47a", mid: "#e6c065", hi: "#f08aa6" },
  },
  cool: {
    name: "Cool — cyan & violet",
    accent: "#6cc4d8",
    highlight: "#bb88e0",
    viz: { lo: "#6cc4d8", mid: "#8a9cf0", hi: "#bb88e0" },
  },
  warm: {
    name: "Warm — coral & rose",
    accent: "#f0a070",
    highlight: "#ff7a8a",
    viz: { lo: "#f8d878", mid: "#f0a070", hi: "#e07090" },
  },
  mono: {
    name: "Mono + lime accent",
    accent: "#c4ff5e",
    highlight: "#c4ff5e",
    viz: { lo: "#9aa0a8", mid: "#c4c8ce", hi: "#c4ff5e" },
  },
};

function App() {
  const { ARTISTS, STATIONS, LYRICS_BOADICEA } = window.MK_DATA;
  const [t, setT] = window.useTweaks(TWEAK_DEFAULTS);
  const tweak = (k, v) => setT(typeof k === "object" ? k : { [k]: v });

  // Session state
  const [authed, setAuthed] = uS(false);
  const [user, setUser] = uS("admin");

  // Browse selection
  const [section, setSection] = uS("library"); // "library" | "stations" | "starred"
  const [artistId, setArtistId] = uS(null);
  const [albumId, setAlbumId] = uS(null);

  // Star state (per-track keyed "artistId/albumId/trackN")
  const [starred, setStarred] = uS(() => {
    const s = new Set();
    ARTISTS.forEach((a) =>
      a.albums.forEach((al) =>
        al.tracks.forEach((tr) => { if (tr.starred) s.add(`${a.id}/${al.id}/${tr.n}`); })
      )
    );
    return s;
  });
  const isStarred = (key) => starred.has(key);
  const toggleStar = (key) => {
    setStarred((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
    // WIRING: forward to /rest/star or /rest/unstar so the server
    // sees the change too. `key` is "artistId/albumId/trackN" — we
    // resolve back to the real song id via the cached library tree.
    if (!window.MK_API || !window.MK_SESSION) return;
    const [aId, alId, trN] = key.split("/");
    const a = window.MK_DATA.ARTISTS.find((x) => x.id === aId);
    const al = a?.albums.find((x) => x.id === alId);
    const tr = al?.tracks.find((x) => String(x.n) === trN);
    if (!tr?.trackId) return;
    const wasStarred = starred.has(key);
    const fn = wasStarred ? window.MK_API.unstar : window.MK_API.star;
    fn(window.MK_SESSION, { id: tr.trackId }).catch((err) =>
      console.warn("[wiring] star/unstar failed:", err)
    );
  };

  // Playback state
  const [playing, setPlaying] = uS(false);
  const [muted, setMuted] = uS(false);
  const [vol, setVol] = uS(0.85);
  const [pos, setPos] = uS(34);    // seconds
  const [dur, setDur] = uS(212);   // seconds
  const [now, setNow] = uS(null);  // { artistId, albumId, trackN, station? }
  const [shuffle, setShuffle] = uS(false);
  const [repeat, setRepeat] = uS("off"); // off | album | track

  // Overlays
  const [showShortcuts, setShowShortcuts] = uS(false);
  const [showPalette, setShowPalette] = uS(false);
  const [showLyrics, setShowLyrics] = uS(false);
  const [fullscreenViz, setFullscreenViz] = uS(false);
  const [showConn, setShowConn] = uS(false);

  // Search
  const [q, setQ] = uS("");
  const [searchOpen, setSearchOpen] = uS(false);
  const searchInputRef = uR(null);

  // Loading state (one-shot — flips after a moment so list goes from skeleton to data).
  const [loaded, setLoaded] = uS(false);
  uE(() => {
    if (!authed) { setLoaded(false); return; }
    const t = setTimeout(() => setLoaded(true), 750);
    return () => clearTimeout(t);
  }, [authed]);

  // === WIRING (added on top of the designer artifact) ===
  // The original artifact ran a `setInterval` to fake a playback clock
  // because the design preview had no audio. Now `_audio.js` owns a
  // real `<audio>` element and `_api.js` builds Subsonic stream URLs.
  // These effects bridge React state <-> that audio element. Keep this
  // block together so the next design-zip drop is easy to forward-port.

  // handleNext is declared below; ref it so the audio-ended listener,
  // bound once, doesn't capture a stale first-render closure.
  const handleNextRef = uR(null);

  // Load the right URL whenever `now` changes (track click, prev/next,
  // station click, search-result pick all funnel through here).
  uE(() => {
    if (!now || !window.MK_AUDIO) return;
    if (now.stationId) {
      const st = window.MK_DATA.STATIONS.find((s) => s.id === now.stationId);
      if (st?.streamUrl) window.MK_AUDIO.load(st.streamUrl);
    } else if (window.MK_SESSION) {
      const a = window.MK_DATA.ARTISTS.find((x) => x.id === now.artistId);
      const al = a?.albums.find((x) => x.id === now.albumId);
      const tr = al?.tracks.find((x) => x.n === now.trackN);
      if (tr?.trackId) {
        window.MK_AUDIO.load(window.MK_API.streamUrl(window.MK_SESSION, tr.trackId));
      }
    }
  }, [now]);

  // Forward play/pause state to the audio element.
  uE(() => {
    if (!window.MK_AUDIO) return;
    if (playing) window.MK_AUDIO.play();
    else window.MK_AUDIO.pause();
  }, [playing]);

  // Forward volume / muted to the audio element.
  uE(() => { window.MK_AUDIO?.setVolume(vol); }, [vol]);
  uE(() => { window.MK_AUDIO?.setMuted(muted); }, [muted]);

  // Real audio time/duration/end -> React state.
  uE(() => {
    if (!window.MK_AUDIO) return;
    const offT = window.MK_AUDIO.onTimeUpdate((t) => setPos(t));
    const offD = window.MK_AUDIO.onDurationChange((d) => { if (d > 0) setDur(d); });
    const offE = window.MK_AUDIO.onEnded(() => handleNextRef.current?.());
    return () => { offT(); offD(); offE(); };
  }, []);

  // Theme toggle on root.
  uE(() => {
    document.documentElement.dataset.theme = t.theme || "dark";
    document.documentElement.dataset.density = t.density || "comfortable";
    document.documentElement.dataset.layout = t.layout || "topband";
    document.documentElement.dataset.preset = t.preset || "tokyo";
    document.documentElement.dataset.lcd = t.lcd ? "on" : "off";
    document.documentElement.dataset.lcdColor = t.lcdColor || "green";
    document.documentElement.style.setProperty("--mk-font-scale", String(t.fontScale || 1));
    const p = PALETTES[t.accent] || PALETTES.tokyo;
    document.documentElement.style.setProperty("--mk-accent", p.accent);
    document.documentElement.style.setProperty("--mk-highlight", p.highlight);
  }, [t.theme, t.density, t.layout, t.fontScale, t.accent, t.preset, t.lcd, t.lcdColor]);

  // Derived data
  const artist = uM(() => ARTISTS.find((a) => a.id === artistId) || null, [artistId]);
  const album = uM(() => artist?.albums.find((al) => al.id === albumId) || null, [artist, albumId]);
  const nowArtist = uM(() => now?.artistId ? ARTISTS.find((a) => a.id === now.artistId) : null, [now]);
  const nowAlbum = uM(() => nowArtist?.albums.find((al) => al.id === now?.albumId) || null, [nowArtist, now]);
  const nowTrack = uM(() => nowAlbum?.tracks.find((t2) => t2.n === now?.trackN) || null, [nowAlbum, now]);
  const nowStation = uM(() => now?.stationId ? STATIONS.find((s) => s.id === now.stationId) : null, [now]);

  // Starred-tracks roll-up
  const starredTracks = uM(() => {
    const out = [];
    ARTISTS.forEach((a) => a.albums.forEach((al) => al.tracks.forEach((tr) => {
      const key = `${a.id}/${al.id}/${tr.n}`;
      if (starred.has(key)) out.push({ artistId: a.id, artistName: a.name, albumId: al.id, albumName: al.name, ...tr, key });
    })));
    return out;
  }, [starred]);

  // Search results
  const results = uM(() => {
    if (!q.trim()) return { artists: [], albums: [], tracks: [] };
    const ql = q.trim().toLowerCase();
    const arts = ARTISTS.filter((a) => a.name.toLowerCase().includes(ql));
    const albs = [];
    const trks = [];
    ARTISTS.forEach((a) => a.albums.forEach((al) => {
      if (al.name.toLowerCase().includes(ql)) albs.push({ ...al, artistId: a.id, artistName: a.name });
      al.tracks.forEach((tr) => {
        if (tr.title.toLowerCase().includes(ql)) trks.push({ ...tr, artistId: a.id, artistName: a.name, albumId: al.id, albumName: al.name });
      });
    }));
    return { artists: arts, albums: albs, tracks: trks };
  }, [q]);

  // Actions
  const playTrack = (artistId, albumId, trackN) => {
    const a = ARTISTS.find((x) => x.id === artistId);
    const al = a?.albums.find((x) => x.id === albumId);
    const tr = al?.tracks.find((x) => x.n === trackN);
    if (!tr) return;
    setNow({ artistId, albumId, trackN });
    setPos(0);
    setDur(parseDur(tr.time));
    setPlaying(true);
  };
  const playStation = (stationId) => {
    setNow({ stationId });
    setPos(0);
    setDur(0); // live stream
    setPlaying(true);
  };
  const handlePlayPause = () => { if (now) setPlaying((p) => !p); };
  const handleNext = () => {
    if (now?.stationId) return;
    if (!nowAlbum) return;
    const idx = nowAlbum.tracks.findIndex((tr) => tr.n === now.trackN);
    const next = nowAlbum.tracks[(idx + 1) % nowAlbum.tracks.length];
    setNow({ ...now, trackN: next.n });
    setPos(0); setDur(parseDur(next.time));
  };
  const handlePrev = () => {
    if (now?.stationId) return;
    if (!nowAlbum) return;
    const idx = nowAlbum.tracks.findIndex((tr) => tr.n === now.trackN);
    const prev = nowAlbum.tracks[(idx - 1 + nowAlbum.tracks.length) % nowAlbum.tracks.length];
    setNow({ ...now, trackN: prev.n });
    setPos(0); setDur(parseDur(prev.time));
  };

  // WIRING: keep the ref the audio-ended listener uses in sync with
  // the latest handleNext closure (which captures the current `now`).
  handleNextRef.current = handleNext;

  // WIRING: on first mount, see if there's a saved session and skip
  // the login form entirely if MK_DATA is already populated for it.
  uE(() => {
    if (authed || !window.MK_RESUME) return;
    let cancelled = false;
    window.MK_RESUME().then((session) => {
      if (cancelled || !session) return;
      setAuthed(true);
      setUser(session.user);
    });
    return () => { cancelled = true; };
  }, []);

  // Keyboard shortcuts
  uE(() => {
    if (!authed) return;
    const onKey = (e) => {
      const target = e.target;
      const isInput = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
      const meta = e.metaKey || e.ctrlKey;
      if (meta && (e.key === "p" || e.key === "P") && !e.shiftKey) {
        e.preventDefault(); setShowPalette((v) => !v); return;
      }
      if (isInput) return;
      switch (e.key) {
        case " ": e.preventDefault(); handlePlayPause(); break;
        case "n": handleNext(); break;
        case "p": handlePrev(); break;
        case "m": setMuted((m) => !m); break;
        case "f": setFullscreenViz((v) => !v); break;
        case "l": setShowLyrics((v) => !v); break;
        case "?": setShowShortcuts((v) => !v); break;
        case "/": e.preventDefault(); searchInputRef.current?.focus(); setSearchOpen(true); break;
        case "Escape":
          if (fullscreenViz) setFullscreenViz(false);
          else if (showShortcuts) setShowShortcuts(false);
          else if (showPalette) setShowPalette(false);
          else if (showLyrics) setShowLyrics(false);
          else if (searchOpen) { setSearchOpen(false); setQ(""); }
          break;
        case "ArrowLeft": setPos((p) => Math.max(0, p - 5)); break;
        case "ArrowRight": setPos((p) => Math.min(dur, p + 5)); break;
        case "ArrowUp": setVol((v) => Math.min(1, v + 0.05)); setMuted(false); e.preventDefault(); break;
        case "ArrowDown": setVol((v) => Math.max(0, v - 0.05)); e.preventDefault(); break;
        case "s": setShuffle((s) => !s); break;
        case "r": setRepeat((r) => r === "off" ? "album" : r === "album" ? "track" : "off"); break;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [authed, now, dur, fullscreenViz, showShortcuts, showPalette, showLyrics, searchOpen]);

  // Sign out
  const signOut = () => {
    setAuthed(false);
    setNow(null);
    setPlaying(false);
    setArtistId(null); setAlbumId(null);
  };

  // Palette command runner — minimal subset that maps to existing actions.
  const runCmd = (c) => {
    if (c.label === "Play / pause") handlePlayPause();
    else if (c.label === "Next track") handleNext();
    else if (c.label === "Previous track") handlePrev();
    else if (c.label === "Volume up") setVol((v) => Math.min(1, v + 0.1));
    else if (c.label === "Volume down") setVol((v) => Math.max(0, v - 0.1));
    else if (c.label === "Toggle lyrics") setShowLyrics((v) => !v);
    else if (c.label === "Toggle visualizer") setFullscreenViz((v) => !v);
    else if (c.label === "Focus search") { searchInputRef.current?.focus(); setSearchOpen(true); }
    else if (c.label === "Show keyboard shortcuts") setShowShortcuts(true);
    else if (c.label === "Toggle shuffle") setShuffle((s) => !s);
    else if (c.label.startsWith("Cycle repeat")) setRepeat((r) => r === "off" ? "album" : r === "album" ? "track" : "off");
    else if (c.label === "Star current track" && now && !now.stationId) toggleStar(`${now.artistId}/${now.albumId}/${now.trackN}`);
    else if (c.label === "Go to Starred") { setSection("starred"); setArtistId(null); setAlbumId(null); }
    else if (c.label === "Go to Stations") { setSection("stations"); setArtistId(null); setAlbumId(null); }
    else if (c.label === "Sign out") signOut();
  };

  // Search-result pick
  const pickSearchResult = (r) => {
    if (r.kind === "artist") { setSection("library"); setArtistId(r.id); setAlbumId(null); }
    else if (r.kind === "album") { setSection("library"); setArtistId(r.artistId); setAlbumId(r.id); }
    else if (r.kind === "track") { setSection("library"); setArtistId(r.artistId); setAlbumId(r.albumId); playTrack(r.artistId, r.albumId, r.trackN); }
    setSearchOpen(false); setQ("");
  };

  if (!authed) {
    return (
      <>
        <window.MK_LoginView themeMode={t.theme} onConnect={({ url, user }) => { setAuthed(true); setUser(user); }} />
        <window.MK_TweaksControls tweak={tweak} t={t} />
      </>
    );
  }

  const palette = PALETTES[t.accent] || PALETTES.tokyo;

  return (
    <div className="mk-shell" data-layout={t.layout}>
      <window.MK_TopBar
        user={user}
        q={q} setQ={(v) => { setQ(v); setSearchOpen(true); }}
        onFocusSearch={() => setSearchOpen(true)}
        onSignOut={signOut}
        searchInputRef={searchInputRef}
      />

      {showConn && (
        <window.MK_ConnectionBanner
          message="GET /rest/getArtists timed out after 8s. Will retry automatically."
          onRetry={() => setShowConn(false)}
          onDismiss={() => setShowConn(false)}
        />
      )}

      <window.MK_MainArea
        t={t}
        section={section} setSection={(s) => { setSection(s); setArtistId(null); setAlbumId(null); }}
        loaded={loaded}
        ARTISTS={ARTISTS}
        STATIONS={STATIONS}
        artist={artist} artistId={artistId} setArtistId={(id) => { setArtistId(id); setAlbumId(null); }}
        album={album} albumId={albumId} setAlbumId={setAlbumId}
        playTrack={playTrack}
        playStation={playStation}
        now={now} nowTrack={nowTrack} nowStation={nowStation}
        starredTracks={starredTracks}
        isStarred={isStarred} toggleStar={toggleStar}
        playing={playing} setPlaying={setPlaying}
        muted={muted} setMuted={setMuted}
        vol={vol} setVol={setVol}
        pos={pos} setPos={setPos} dur={dur}
        handlePlayPause={handlePlayPause} handleNext={handleNext} handlePrev={handlePrev}
        nowArtist={nowArtist} nowAlbum={nowAlbum}
        palette={palette}
        fullscreenViz={fullscreenViz} setFullscreenViz={setFullscreenViz}
        shuffle={shuffle} repeat={repeat}
        setShowLyrics={setShowLyrics}
      />

      <window.MK_SearchDropdown
        q={searchOpen ? q : ""}
        results={results}
        anchorEl={searchInputRef.current}
        onPick={pickSearchResult}
        onClose={() => setSearchOpen(false)}
      />
      <window.MK_ShortcutsOverlay open={showShortcuts} onClose={() => setShowShortcuts(false)} />
      <window.MK_CommandPalette open={showPalette} onClose={() => setShowPalette(false)} onRun={runCmd} />
      <window.MK_LyricsOverlay
        open={showLyrics && !!nowTrack}
        onClose={() => setShowLyrics(false)}
        title={nowTrack?.title || ""}
        artist={nowArtist?.name || ""}
        lines={LYRICS_BOADICEA}
      />
      {fullscreenViz && (
        <window.MK_FullscreenViz
          onClose={() => setFullscreenViz(false)}
          vizStyle={t.viz}
          running={playing}
          palette={palette}
          nowTrack={nowTrack} nowArtist={nowArtist} nowAlbum={nowAlbum}
          nowStation={nowStation}
          pos={pos} dur={dur}
          playing={playing} onPlayPause={handlePlayPause} onPrev={handlePrev} onNext={handleNext}
        />
      )}

      <window.MK_TweaksControls tweak={tweak} t={t} setShowConn={setShowConn} />
    </div>
  );
}

function parseDur(s) {
  if (!s) return 0;
  const [m, sec] = s.split(":").map(Number);
  return m * 60 + sec;
}

function fmtDur(s) {
  if (!s || !isFinite(s)) return "00:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
}

window.MK_App = App;
window.MK_fmtDur = fmtDur;
window.MK_parseDur = parseDur;
window.MK_PALETTES = PALETTES;
