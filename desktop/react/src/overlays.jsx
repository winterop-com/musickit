// Overlays: keyboard-shortcuts panel, command palette, search dropdown, lyrics.

const { useState, useEffect, useRef, useMemo } = React;

const SHORTCUTS = [
  { keys: ["Space"], label: "Play / pause" },
  { keys: ["n"], label: "Next track" },
  { keys: ["p"], label: "Previous track" },
  { keys: ["←", "→"], sep: "/", label: "Seek −5s / +5s" },
  { keys: ["↑", "↓"], sep: "/", label: "Volume up / down" },
  { keys: ["m"], label: "Mute / unmute" },
  { keys: ["f"], label: "Fullscreen visualizer" },
  { keys: ["l"], label: "Toggle lyrics" },
  { keys: ["/"], label: "Focus filter" },
  { keys: ["⌘", "P"], sep: "+", label: "Command palette" },
  { keys: ["Esc"], label: "Close modal / exit fullscreen" },
  { keys: ["?"], label: "Toggle this panel" },
];

function Kbd({ k }) {
  return <span className="kbd">{k}</span>;
}

function ShortcutsOverlay({ open, onClose }) {
  if (!open) return null;
  return (
    <div className="mk-modal-scrim" onClick={onClose}>
      <div className="mk-modal" onClick={(e) => e.stopPropagation()}>
        <div className="mk-modal-title">Keyboard shortcuts</div>
        <div className="mk-shortcut-list">
          {SHORTCUTS.map((s, i) => (
            <div className="mk-shortcut-row" key={i}>
              <div className="mk-shortcut-keys">
                {s.keys.map((k, j) => (
                  <React.Fragment key={j}>
                    {j > 0 && <span className="mk-sep">{s.sep || ""}</span>}
                    <Kbd k={k} />
                  </React.Fragment>
                ))}
              </div>
              <div className="mk-shortcut-label">{s.label}</div>
            </div>
          ))}
        </div>
        <div className="mk-modal-foot">Click anywhere or press Esc to close.</div>
      </div>
    </div>
  );
}

const PALETTE_CMDS = [
  { label: "Play / pause", k: "space" },
  { label: "Next track", k: "n" },
  { label: "Previous track", k: "p" },
  { label: "Volume up", k: "0" },
  { label: "Volume down", k: "9" },
  { label: "Seek backward 5s", k: "<" },
  { label: "Seek forward 5s", k: ">" },
  { label: "Cycle repeat (off / album / track)", k: "r" },
  { label: "Toggle shuffle", k: "s" },
  { label: "Toggle lyrics", k: "l" },
  { label: "Toggle visualizer", k: "f" },
  { label: "Focus search", k: "/" },
  { label: "Show keyboard shortcuts", k: "?" },
  { label: "Star current track", k: "*" },
  { label: "Go to Starred", k: "g s" },
  { label: "Go to Stations", k: "g r" },
  { label: "Sign out", k: "⌘ Q" },
];

function CommandPalette({ open, onClose, onRun }) {
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const inpRef = useRef(null);
  useEffect(() => {
    if (open) {
      setQ("");
      setSel(0);
      setTimeout(() => inpRef.current?.focus(), 30);
    }
  }, [open]);
  const list = useMemo(() => {
    if (!q.trim()) return PALETTE_CMDS;
    const ql = q.toLowerCase();
    return PALETTE_CMDS.filter((c) => c.label.toLowerCase().includes(ql));
  }, [q]);
  if (!open) return null;
  return (
    <div className="mk-modal-scrim" onClick={onClose}>
      <div className="mk-palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inpRef}
          className="mk-palette-input"
          placeholder="type a command…"
          value={q}
          onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { setSel((s) => Math.min(list.length - 1, s + 1)); e.preventDefault(); }
            else if (e.key === "ArrowUp") { setSel((s) => Math.max(0, s - 1)); e.preventDefault(); }
            else if (e.key === "Enter") { if (list[sel]) { onRun(list[sel]); onClose(); } }
            else if (e.key === "Escape") { onClose(); }
          }}
        />
        <div className="mk-palette-list">
          {list.map((c, i) => (
            <div
              key={c.label}
              className={"mk-palette-row" + (i === sel ? " sel" : "")}
              onMouseEnter={() => setSel(i)}
              onClick={() => { onRun(c); onClose(); }}
            >
              <div className="mk-palette-label">{c.label}</div>
              <div className="mk-palette-key">{c.k}</div>
            </div>
          ))}
          {list.length === 0 && <div className="mk-palette-empty">no matches</div>}
        </div>
      </div>
    </div>
  );
}

function SearchDropdown({ q, results, onPick, onClose, anchorEl }) {
  if (!q || !anchorEl) return null;
  const rect = anchorEl.getBoundingClientRect();
  const style = {
    position: "fixed",
    top: rect.bottom + 6,
    left: rect.left,
    width: rect.width,
  };
  const empty = !results.artists.length && !results.albums.length && !results.tracks.length;
  return (
    <>
      <div className="mk-search-shield" onClick={onClose} />
      <div className="mk-search-dropdown" style={style}>
        {empty && <div className="mk-search-empty">No results for “{q}”.<div className="mk-search-empty-sub">Try a different spelling or a shorter query.</div></div>}
        {results.artists.length > 0 && (
          <>
            <div className="mk-search-section">ARTISTS</div>
            {results.artists.slice(0, 3).map((a) => (
              <div key={a.id} className="mk-search-row" onClick={() => onPick({ kind: "artist", id: a.id })}>
                <div className="mk-search-row-title">{a.name}</div>
              </div>
            ))}
          </>
        )}
        {results.albums.length > 0 && (
          <>
            <div className="mk-search-section">ALBUMS</div>
            {results.albums.slice(0, 3).map((al) => (
              <div key={al.id} className="mk-search-row" onClick={() => onPick({ kind: "album", artistId: al.artistId, id: al.id })}>
                <div className="mk-search-row-title">{al.name}</div>
                <div className="mk-search-row-sub">{al.artistName} · {al.year}</div>
              </div>
            ))}
          </>
        )}
        {results.tracks.length > 0 && (
          <>
            <div className="mk-search-section">TRACKS</div>
            {results.tracks.slice(0, 8).map((t, i) => (
              <div key={i} className="mk-search-row" onClick={() => onPick({ kind: "track", artistId: t.artistId, albumId: t.albumId, trackN: t.n })}>
                <div className="mk-search-row-title">{t.title}</div>
                <div className="mk-search-row-sub">{t.artistName} · {t.albumName}</div>
              </div>
            ))}
          </>
        )}
      </div>
    </>
  );
}

function LyricsOverlay({ open, onClose, lines, title, artist }) {
  if (!open) return null;
  return (
    <div className="mk-lyrics-scrim" onClick={onClose}>
      <div className="mk-lyrics" onClick={(e) => e.stopPropagation()}>
        <div className="mk-lyrics-head">
          <div className="mk-lyrics-title">{title}</div>
          <div className="mk-lyrics-artist">{artist}</div>
          <button className="mk-lyrics-close" onClick={onClose}>×</button>
        </div>
        <div className="mk-lyrics-body">
          {lines.map((l, i) => (
            <div key={i} className={"mk-lyrics-line" + (l === "" ? " gap" : "")}>{l || "\u00A0"}</div>
          ))}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { MK_ShortcutsOverlay: ShortcutsOverlay, MK_CommandPalette: CommandPalette, MK_SearchDropdown: SearchDropdown, MK_LyricsOverlay: LyricsOverlay });
