"""Cover-art LRU: hit/miss/eviction/invalidation."""

from __future__ import annotations

import io
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from musickit.library.models import LibraryAlbum, LibraryIndex, LibraryTrack
from musickit.serve import ServeConfig, create_app
from musickit.serve.cover_cache import CoverCache
from musickit.serve.ids import album_id
from musickit.serve.index import IndexCache


def _params(**extra: str | int) -> dict[str, str | int]:
    return {"u": "mort", "p": "secret", "f": "json", **extra}


def _album_with_sidecar(tmp_path: Path, color: tuple[int, int, int] = (255, 0, 0)) -> LibraryAlbum:
    album_dir = tmp_path / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    img = Image.new("RGB", (100, 100), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    (album_dir / "cover.jpg").write_bytes(buf.getvalue())
    track = LibraryTrack(path=album_dir / "01.flac", title="T", track_no=1)
    return LibraryAlbum(
        path=album_dir,
        artist_dir="Artist",
        album_dir="Album",
        tag_album="Album",
        track_count=1,
        has_cover=True,
        tracks=[track],
    )


def _client(tmp_path: Path, album: LibraryAlbum) -> tuple[TestClient, IndexCache]:
    cfg = ServeConfig(username="mort", password="secret")
    app = create_app(root=tmp_path, cfg=cfg)
    cache: IndexCache = app.state.cache
    cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    return TestClient(app), cache


# ---------------------------------------------------------------------------
# CoverCache unit tests — direct LRU exercise without FastAPI plumbing
# ---------------------------------------------------------------------------


def test_put_then_get_returns_same_bytes() -> None:
    cache = CoverCache(max_bytes=1024)
    cache.put(("k", None), b"abc", "image/jpeg")
    assert cache.get(("k", None)) == (b"abc", "image/jpeg")
    assert cache.hits == 1
    assert cache.misses == 0


def test_get_miss_increments_counter() -> None:
    cache = CoverCache(max_bytes=1024)
    assert cache.get(("missing", None)) is None
    assert cache.misses == 1


def test_lru_eviction_drops_oldest_first() -> None:
    cache = CoverCache(max_bytes=10)  # tiny budget to force eviction
    cache.put(("a", None), b"aaaa", "image/jpeg")  # 4 bytes
    cache.put(("b", None), b"bbbb", "image/jpeg")  # 8 bytes total
    cache.put(("c", None), b"cccc", "image/jpeg")  # 12 → evicts ("a")
    assert cache.get(("a", None)) is None
    assert cache.get(("b", None)) == (b"bbbb", "image/jpeg")
    assert cache.get(("c", None)) == (b"cccc", "image/jpeg")
    assert cache.evictions == 1


def test_get_promotes_to_mru() -> None:
    cache = CoverCache(max_bytes=10)
    cache.put(("a", None), b"aaaa", "image/jpeg")
    cache.put(("b", None), b"bbbb", "image/jpeg")
    # Read "a" → makes it MRU.
    cache.get(("a", None))
    cache.put(("c", None), b"cccc", "image/jpeg")  # evicts "b" instead of "a"
    assert cache.get(("a", None)) is not None
    assert cache.get(("b", None)) is None


def test_payload_larger_than_budget_skipped() -> None:
    cache = CoverCache(max_bytes=4)
    cache.put(("big", None), b"abcdefgh", "image/jpeg")  # 8 bytes > 4
    assert cache.get(("big", None)) is None
    assert len(cache) == 0


def test_clear_drops_everything() -> None:
    cache = CoverCache(max_bytes=1024)
    cache.put(("a", None), b"aaa", "image/jpeg")
    cache.put(("b", None), b"bbb", "image/jpeg")
    cache.clear()
    assert len(cache) == 0
    assert cache.used_bytes == 0


def test_replace_existing_key_updates_byte_total() -> None:
    cache = CoverCache(max_bytes=1024)
    cache.put(("a", None), b"short", "image/jpeg")
    cache.put(("a", None), b"longer-payload", "image/jpeg")
    assert cache.get(("a", None)) == (b"longer-payload", "image/jpeg")
    assert cache.used_bytes == len(b"longer-payload")
    assert len(cache) == 1


# ---------------------------------------------------------------------------
# Endpoint integration — the cache actually short-circuits Pillow
# ---------------------------------------------------------------------------


def test_endpoint_caches_response(tmp_path: Path) -> None:
    album = _album_with_sidecar(tmp_path)
    client, cache = _client(tmp_path, album)
    aid = album_id(album)

    response = client.get("/rest/getCoverArt", params=_params(id=aid))
    assert response.status_code == 200
    # The cache should now have the unsized entry.
    assert cache.cover_cache.get((aid, None)) is not None
    pre_hits = cache.cover_cache.hits

    # Second request → cache hit.
    response = client.get("/rest/getCoverArt", params=_params(id=aid))
    assert response.status_code == 200
    # Cache get() inside endpoint counts as one hit; our explicit get above
    # also counted, so just assert "increased."
    assert cache.cover_cache.hits > pre_hits


def test_endpoint_resize_cached_separately(tmp_path: Path) -> None:
    """`?size=50` and unsized are separate cache entries."""
    album = _album_with_sidecar(tmp_path)
    client, cache = _client(tmp_path, album)
    aid = album_id(album)

    client.get("/rest/getCoverArt", params=_params(id=aid))
    client.get("/rest/getCoverArt", params=_params(id=aid, size=50))

    assert cache.cover_cache.get((aid, None)) is not None
    assert cache.cover_cache.get((aid, 50)) is not None


def test_reindex_clears_cover_cache(tmp_path: Path) -> None:
    """A library rebuild must drop cached covers — sidecar may have changed."""
    album = _album_with_sidecar(tmp_path)
    client, cache = _client(tmp_path, album)
    aid = album_id(album)

    client.get("/rest/getCoverArt", params=_params(id=aid))
    assert len(cache.cover_cache) == 1

    cache._reindex(LibraryIndex(root=tmp_path, albums=[album]))  # noqa: SLF001
    assert len(cache.cover_cache) == 0
