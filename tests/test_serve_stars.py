"""StarStore — TOML-backed favourites store at `<root>/.musickit/stars.toml`."""

from __future__ import annotations

import re
from pathlib import Path

from musickit.serve.stars import StarStore


def test_empty_store_round_trips(tmp_path: Path) -> None:
    """Fresh store from a missing file is empty; reload-after-save matches."""
    s = StarStore(tmp_path / "stars.toml")
    assert s.all_ids() == {}
    assert not s.is_starred("tr_x")
    assert s.starred_at("tr_x") is None


def test_add_remove_round_trip(tmp_path: Path) -> None:
    """`add` then `remove` returns to empty; `add` is idempotent."""
    s = StarStore(tmp_path / "stars.toml")
    s.add("tr_001")
    assert s.is_starred("tr_001")
    assert s.starred_at("tr_001") is not None
    # Idempotent — second add doesn't add a duplicate or overwrite the timestamp.
    first_ts = s.starred_at("tr_001")
    s.add("tr_001")
    assert s.starred_at("tr_001") == first_ts

    s.remove("tr_001")
    assert not s.is_starred("tr_001")
    # Idempotent removal.
    s.remove("tr_001")
    assert s.all_ids() == {}


def test_starred_at_uses_iso_8601_utc(tmp_path: Path) -> None:
    """Timestamps follow the Subsonic-compatible ISO format."""
    s = StarStore(tmp_path / "stars.toml")
    s.add("tr_001")
    ts = s.starred_at("tr_001")
    assert ts is not None
    # Format like 2026-05-05T10:30:00Z.
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts) is not None


def test_by_kind_filters_by_id_prefix(tmp_path: Path) -> None:
    """`by_kind` partitions IDs by their `ar_` / `al_` / `tr_` prefix."""
    s = StarStore(tmp_path / "stars.toml")
    s.add("ar_111")
    s.add("al_222")
    s.add("tr_333")
    s.add("tr_444")
    assert set(s.by_kind("ar_").keys()) == {"ar_111"}
    assert set(s.by_kind("al_").keys()) == {"al_222"}
    assert set(s.by_kind("tr_").keys()) == {"tr_333", "tr_444"}


def test_persistence_across_instances(tmp_path: Path) -> None:
    """A second StarStore at the same path reads what the first wrote."""
    p = tmp_path / "stars.toml"
    s1 = StarStore(p)
    s1.add("tr_001")
    s1.add("al_002")

    s2 = StarStore(p)
    assert s2.is_starred("tr_001")
    assert s2.is_starred("al_002")
    assert s2.starred_at("tr_001") == s1.starred_at("tr_001")


def test_corrupt_file_starts_empty(tmp_path: Path) -> None:
    """Garbled TOML must not crash — silently treat as empty."""
    p = tmp_path / "stars.toml"
    p.write_text("this is = not [valid toml ===\n", encoding="utf-8")
    s = StarStore(p)
    assert s.all_ids() == {}


def test_for_root_canonical_path(tmp_path: Path) -> None:
    """`StarStore.for_root(root)` resolves to `<root>/.musickit/stars.toml`."""
    root = tmp_path / "lib"
    root.mkdir()
    s = StarStore.for_root(root)
    assert s.path == root / ".musickit" / "stars.toml"


def test_prune_removes_stale_ids(tmp_path: Path) -> None:
    """`prune(valid_ids)` drops IDs not in the set; returns count removed."""
    s = StarStore(tmp_path / "stars.toml")
    s.add("tr_001")
    s.add("tr_002")
    s.add("ar_003")

    removed = s.prune({"tr_001"})
    assert removed == 2
    assert s.is_starred("tr_001")
    assert not s.is_starred("tr_002")
    assert not s.is_starred("ar_003")
