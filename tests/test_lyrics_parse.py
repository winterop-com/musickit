"""Lyrics LRC parser — `[mm:ss.xx] line` shapes."""

from __future__ import annotations

from musickit.lyrics import LrcLine, is_synced, parse_lrc


def test_parse_standard_lrc() -> None:
    body = "[00:01.50]Hello\n[00:03.10]World\n"
    lines = parse_lrc(body)
    assert lines == [
        LrcLine(start_ms=1500, text="Hello"),
        LrcLine(start_ms=3100, text="World"),
    ]
    assert is_synced(lines) is True


def test_parse_no_fraction() -> None:
    body = "[01:23]No fraction here"
    lines = parse_lrc(body)
    assert lines == [LrcLine(start_ms=83_000, text="No fraction here")]


def test_metadata_headers_are_dropped() -> None:
    body = "[ar: ABBA]\n[ti: Dancing Queen]\n[length: 03:50]\n[00:01.00]Friday night"
    lines = parse_lrc(body)
    assert lines == [LrcLine(start_ms=1_000, text="Friday night")]


def test_plain_text_kept_unsynced() -> None:
    body = "Hello world\nNo timestamps here"
    lines = parse_lrc(body)
    assert lines == [
        LrcLine(start_ms=0, text="Hello world"),
        LrcLine(start_ms=0, text="No timestamps here"),
    ]
    assert is_synced(lines) is False


def test_multi_timestamp_line_yields_one_line_per_timestamp() -> None:
    body = "[00:01.00][00:05.00]repeat me"
    lines = parse_lrc(body)
    assert lines == [
        LrcLine(start_ms=1_000, text="repeat me"),
        LrcLine(start_ms=5_000, text="repeat me"),
    ]


def test_empty_input() -> None:
    assert parse_lrc("") == []
    assert is_synced([]) is False
