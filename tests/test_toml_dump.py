"""In-repo TOML writer (`musickit._toml_dump`).

Replaces the `tomli-w` dep for the two narrow shapes we serialise
(stars.toml and state.toml) plus the `musickit config migrate` output.
Coverage focuses on round-trip correctness against `tomllib` and on
the edge cases we actively rely on.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from musickit import _toml_dump


def _roundtrip(data: Mapping[str, Any]) -> dict[str, Any]:
    """Serialize via _toml_dump, parse back with stdlib tomllib."""
    # `dumps` accepts a dict; `Mapping` lets test callers pass narrower
    # types (`dict[str, str]`, `dict[str, dict[str, str]]`) without
    # mypy invariance complaints.
    text = _toml_dump.dumps(dict(data))
    return tomllib.loads(text)


# ---------------------------------------------------------------------------
# Shapes we actually use
# ---------------------------------------------------------------------------


def test_flat_top_level_keys() -> None:
    """state.toml's pre-`[subsonic]` shape: top-level scalars only."""
    data = {"theme": "dark", "airplay_device": "Living Room"}
    assert _roundtrip(data) == data


def test_section_with_string_values() -> None:
    """stars.toml shape: `[items]` with str→str entries (id → ISO timestamp)."""
    data = {
        "items": {
            "tr_abc": "2026-05-08T10:30:00Z",
            "al_xyz": "2026-05-08T10:31:15Z",
        }
    }
    assert _roundtrip(data) == data


def test_top_level_scalars_plus_one_section() -> None:
    """state.toml mid-state shape: top-level theme + nested [subsonic] block."""
    data = {
        "theme": "dark",
        "subsonic": {
            "host": "http://laptop:4533",
            "user": "admin",
            "token": "abcdef",
            "salt": "123456",
        },
    }
    assert _roundtrip(data) == data


def test_mixed_value_types() -> None:
    """Bool / int / float / str all round-trip correctly."""
    data = {
        "server": {
            "username": "morten",
            "port": 4533,
            "use_https": True,
            "timeout_s": 5.0,
        }
    }
    assert _roundtrip(data) == data


# ---------------------------------------------------------------------------
# Escaping — strings with special chars must round-trip exactly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        'quoted "string"',
        "back\\slash",
        "with\nnewline",
        "with\ttab",
        "control\x01char",
        "unicode: ♪ Beyoncé Sigur Rós",
        "",  # empty string
    ],
)
def test_string_escaping_roundtrip(value: str) -> None:
    """Tricky strings survive serialise → parse intact."""
    data = {"k": value}
    parsed = _roundtrip(data)
    assert parsed["k"] == value


def test_emit_basic_string_only() -> None:
    """We always emit double-quoted basic strings, never literal/multi-line forms."""
    out = _toml_dump.dumps({"k": "hello"})
    assert 'k = "hello"' in out


# ---------------------------------------------------------------------------
# Boundary errors — unsupported shapes raise TypeError loudly.
# ---------------------------------------------------------------------------


def test_nested_tables_not_supported() -> None:
    """Two-level nesting (e.g. `[server.scrobble]`) is out of scope; raises TypeError."""
    data = {"server": {"scrobble": {"webhook": {"url": "x"}}}}
    with pytest.raises(TypeError, match="nested tables"):
        _toml_dump.dumps(data)


def test_unsupported_value_type_raises() -> None:
    """Lists / datetimes / None aren't supported; raises TypeError early."""
    with pytest.raises(TypeError):
        _toml_dump.dumps({"items": [1, 2, 3]})


# ---------------------------------------------------------------------------
# Key formatting
# ---------------------------------------------------------------------------


def test_bare_key_format() -> None:
    """Keys with letters/digits/_/- emit unquoted."""
    out = _toml_dump.dumps({"my-key_2": "v"})
    assert "my-key_2 = " in out


def test_quoted_key_format() -> None:
    """Keys with non-bare chars get quoted."""
    out = _toml_dump.dumps({"key with space": "v"})
    assert '"key with space" = ' in out


# ---------------------------------------------------------------------------
# File write helper
# ---------------------------------------------------------------------------


def test_dump_path_writes_file(tmp_path: Path) -> None:
    """`dump_path` writes UTF-8 + creates parent dirs as needed."""
    out_path = tmp_path / "nested" / "deeper" / "config.toml"
    _toml_dump.dump_path({"k": "v"}, out_path)
    assert out_path.exists()
    parsed = tomllib.loads(out_path.read_text(encoding="utf-8"))
    assert parsed == {"k": "v"}


def test_dump_path_overwrites_existing(tmp_path: Path) -> None:
    """Re-writing the same path replaces content, doesn't append."""
    p = tmp_path / "config.toml"
    _toml_dump.dump_path({"k": "first"}, p)
    _toml_dump.dump_path({"k": "second"}, p)
    parsed = tomllib.loads(p.read_text(encoding="utf-8"))
    assert parsed == {"k": "second"}


# ---------------------------------------------------------------------------
# Trailing-newline guarantee — TOML files conventionally end in \n.
# ---------------------------------------------------------------------------


def test_output_ends_with_newline() -> None:
    """The serialiser appends a trailing newline so editors don't add their own."""
    out = _toml_dump.dumps({"k": "v"})
    assert out.endswith("\n")
