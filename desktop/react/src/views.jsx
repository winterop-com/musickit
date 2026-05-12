// Login view + miscellaneous small components used across the app.

const { useEffect: useEff_v, useState: useSt_v, useRef: useRef_v } = React;

function LoginView({ onConnect, themeMode }) {
  const [url, setUrl] = useSt_v("http://localhost:4533");
  const [user, setUser] = useSt_v("admin");
  const [pw, setPw] = useSt_v("");
  const [busy, setBusy] = useSt_v(false);
  const [err, setErr] = useSt_v("");

  const submit = (e) => {
    e.preventDefault();
    setErr("");
    if (!url.trim() || !user.trim() || !pw) {
      // Password is part of the Subsonic auth contract — every endpoint
      // needs either `?u=&p=` (plain) or `?u=&t=&s=` (salted token, which
      // we always compute from the password). Without it, the request
      // would 401 and the user would see a less-helpful network error.
      setErr("Server URL, username, and password are all required.");
      return;
    }
    setBusy(true);
    // Wiring layer (`_wiring.jsx`) replaces `props.onConnect` with an
    // async function that does real Subsonic auth + library fetch. The
    // original fake 700ms timeout fired regardless of result; we await
    // the real call instead so "Connecting…" stays on while the network
    // round-trip is in flight.
    Promise.resolve()
      .then(() => onConnect({ url, user, pass: pw }))
      .catch((e) => setErr(String(e?.message || e)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mk-login-shell">
      <div className="mk-login-brand">
        <div className="mk-login-logo">MusicKit</div>
        <div className="mk-login-tag">desktop · v0.22</div>
      </div>
      <form className="mk-login-card" onSubmit={submit}>
        <div className="mk-login-title">Connect to a Subsonic server</div>
        <div className="mk-login-help">
          Works against any spec-compliant server: <code>musickit serve</code>, Navidrome,
          Airsonic, Gonic, etc.
        </div>
        <div className="mk-login-inner">
          <label className="mk-field">
            <span>Server URL</span>
            <input value={url} onChange={(e) => setUrl(e.target.value)} className="mono" />
          </label>
          <label className="mk-field">
            <span>Username</span>
            <input value={user} onChange={(e) => setUser(e.target.value)} className="mono" />
          </label>
          <label className="mk-field">
            <span>Password</span>
            <input type="password" value={pw} onChange={(e) => setPw(e.target.value)} className="mono" />
          </label>
          {err && <div className="mk-login-error">{err}</div>}
          <button type="submit" className="mk-btn-primary" disabled={busy}>
            {busy ? "Connecting…" : "Connect"}
          </button>
          <div className="mk-login-foot">
            Credentials are sent via <code>?u=&amp;p=</code> over your Subsonic endpoint —
            use HTTPS in production.
          </div>
        </div>
      </form>
    </div>
  );
}

// Skeleton row shimmer for loading state.
function SkeletonRow({ width = "100%" }) {
  return <div className="mk-skel" style={{ width }} />;
}

// Star toggle.
function StarBtn({ on, onToggle, size = 16 }) {
  return (
    <button
      className={"mk-star" + (on ? " on" : "")}
      onClick={(e) => { e.stopPropagation(); onToggle(); }}
      aria-label={on ? "Unstar" : "Star"}
      title={on ? "Unstar" : "Star"}
    >
      <svg viewBox="0 0 24 24" width={size} height={size} fill="currentColor">
        <path d="M12 21s-7.5-4.5-9.5-9.5C1 7 4.5 4 8 4c2 0 3.5 1 4 2 .5-1 2-2 4-2 3.5 0 7 3 5.5 7.5C19.5 16.5 12 21 12 21z"/>
      </svg>
    </button>
  );
}

// Connection-error banner (rare path; we don't auto-show it, but it lives in
// the design system so it can be triggered from the command palette).
function ConnectionBanner({ message, onRetry, onDismiss }) {
  return (
    <div className="mk-conn-banner">
      <div className="mk-conn-icon">!</div>
      <div className="mk-conn-text">
        <div className="mk-conn-title">Lost connection to server</div>
        <div className="mk-conn-sub">{message}</div>
      </div>
      <button className="mk-conn-btn" onClick={onRetry}>Retry</button>
      <button className="mk-conn-x" onClick={onDismiss}>×</button>
    </div>
  );
}

Object.assign(window, { MK_LoginView: LoginView, MK_SkeletonRow: SkeletonRow, MK_StarBtn: StarBtn, MK_ConnectionBanner: ConnectionBanner });
