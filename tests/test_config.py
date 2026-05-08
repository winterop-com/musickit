"""Top-level config — `musickit.toml` loading, env-var precedence, legacy fallback."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from musickit.config import (
    AcoustIDSection,
    MusickitConfig,
    ServerSection,
    _load_legacy_serve_toml,
    legacy_serve_path,
    load_config,
    migrate_legacy_config,
    render_config_summary,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no MUSICKIT_* env vars leak between tests."""
    for key in list(os.environ):
        if key.startswith("MUSICKIT_"):
            monkeypatch.delenv(key, raising=False)


def _redirect_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point both config_path() and legacy_serve_path() at tmp_path."""
    monkeypatch.setattr("musickit.config.config_dir", lambda: tmp_path)
    # MusickitConfig caches `toml_file` at class-creation time, so override
    # the class attribute too.
    MusickitConfig.model_config["toml_file"] = str(tmp_path / "musickit.toml")


def test_defaults_when_no_file_no_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Empty environment + no file → admin/admin, no acoustid key."""
    _redirect_config(monkeypatch, tmp_path)
    cfg = load_config()
    assert cfg.server.username == "admin"
    assert cfg.server.password == "admin"
    assert cfg.acoustid.api_key is None


def test_loads_musickit_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hand-written musickit.toml is honoured."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "musickit.toml").write_text(
        '[server]\nusername = "alice"\npassword = "wonder"\n\n[acoustid]\napi_key = "acokey"\n',
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.server.username == "alice"
    assert cfg.server.password == "wonder"
    assert cfg.acoustid.api_key == "acokey"


def test_env_overrides_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`MUSICKIT_SERVER__USERNAME` beats the TOML's `[server].username`."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "musickit.toml").write_text(
        '[server]\nusername = "from-file"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("MUSICKIT_SERVER__USERNAME", "from-env")
    cfg = load_config()
    assert cfg.server.username == "from-env"


def test_legacy_serve_toml_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When musickit.toml is absent but serve.toml exists, parse it."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "serve.toml").write_text(
        'username = "legacy-user"\npassword = "legacy-pass"\n',
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.server.username == "legacy-user"
    assert cfg.server.password == "legacy-pass"
    out = capsys.readouterr().out
    assert "legacy" in out.lower()


def test_legacy_serve_toml_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`_silent=True` suppresses the deprecation hint (used by convert path)."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "serve.toml").write_text('username = "u"\npassword = "p"\n', encoding="utf-8")
    load_config(_silent=True)
    assert capsys.readouterr().out == ""


def test_legacy_scrobble_block_preserved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Scrobble webhook in serve.toml flows through to MusickitConfig."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "serve.toml").write_text(
        'username = "u"\npassword = "p"\n\n[scrobble.webhook]\nurl = "https://hass.lan/api/webhook/musickit"\n',
        encoding="utf-8",
    )
    cfg = _load_legacy_serve_toml()
    assert cfg is not None
    assert cfg.server.scrobble.webhook is not None
    assert cfg.server.scrobble.webhook.url == "https://hass.lan/api/webhook/musickit"


def test_migration_writes_musickit_toml(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`migrate_legacy_config` writes the new file and (optionally) removes the old."""
    _redirect_config(monkeypatch, tmp_path)
    legacy = tmp_path / "serve.toml"
    legacy.write_text('username = "alice"\npassword = "wonder"\n', encoding="utf-8")
    written, deleted = migrate_legacy_config(delete_source=True)
    assert written == tmp_path / "musickit.toml"
    assert deleted == legacy
    assert not legacy.exists()
    assert (tmp_path / "musickit.toml").exists()
    # Re-reading via load_config returns the migrated values.
    cfg = load_config(_silent=True)
    assert cfg.server.username == "alice"


def test_migration_keep_legacy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`delete_source=False` keeps the old file in place."""
    _redirect_config(monkeypatch, tmp_path)
    legacy = tmp_path / "serve.toml"
    legacy.write_text('username = "u"\npassword = "p"\n', encoding="utf-8")
    written, deleted = migrate_legacy_config(delete_source=False)
    assert written is not None
    assert deleted is None
    assert legacy.exists()


def test_migration_idempotent_when_target_exists(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Re-running migration with both files present is a no-op."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "musickit.toml").write_text("", encoding="utf-8")
    (tmp_path / "serve.toml").write_text('username = "x"\n', encoding="utf-8")
    written, deleted = migrate_legacy_config()
    assert written is None
    assert deleted is None


def test_render_config_summary_masks_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The summary never leaks the password / api_key plaintext."""
    _redirect_config(monkeypatch, tmp_path)
    cfg = MusickitConfig(
        server=ServerSection(username="alice", password="super-secret"),
        acoustid=AcoustIDSection(api_key="ackey"),
    )
    out = render_config_summary(cfg)
    assert "super-secret" not in out
    assert "ackey" not in out
    assert "****" in out
    # And the unmasked, non-sensitive value IS visible.
    assert "alice" in out


def test_section_extra_keys_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Forward-compat: unknown TOML keys don't fail validation."""
    _redirect_config(monkeypatch, tmp_path)
    (tmp_path / "musickit.toml").write_text(
        '[server]\nusername = "u"\nfuture_key = "ignore-me"\n',
        encoding="utf-8",
    )
    cfg = load_config()
    assert cfg.server.username == "u"


def test_legacy_path_helper_points_under_config_dir() -> None:
    """`legacy_serve_path()` is `<config_dir>/serve.toml`."""
    assert legacy_serve_path().name == "serve.toml"
