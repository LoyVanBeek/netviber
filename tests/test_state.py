"""Tests for state persistence and re-import cache carry-over."""

import os
import time

import pytest

from netviber.state import READ_MAX_ITEMS, StateStore

TABS = [{"id": "t1", "title": "News"}]
FEEDS = [
    {"id": "f1", "title": "A", "xml_url": "http://a/rss", "html_url": "", "tab_id": "t1"},
    {"id": "f2", "title": "B", "xml_url": "http://b/rss", "html_url": "", "tab_id": "t1"},
]


def seed(tmp_path):
    store = StateStore(tmp_path / "s.json")
    store.replace_subscriptions(TABS, FEEDS)
    store.cache_entry("f1").update(status="ok", items=[{"id": "1", "title": "x", "link": "", "published": None}])
    store.save()
    return store


def test_state_round_trips(tmp_path):
    seed(tmp_path)
    reloaded = StateStore(tmp_path / "s.json")
    assert [f["id"] for f in reloaded.state["feeds"]] == ["f1", "f2"]
    assert reloaded.state["cache"]["f1"]["status"] == "ok"


def test_reimport_keeps_ids_and_cache_for_known_urls(tmp_path):
    seed(tmp_path)
    store = StateStore(tmp_path / "s.json")
    new_feeds = [
        {"id": "fresh", "title": "A2", "xml_url": "http://a/rss", "html_url": "", "tab_id": "t9"},
        {"id": "fresh2", "title": "C", "xml_url": "http://c/rss", "html_url": "", "tab_id": "t9"},
    ]
    store.replace_subscriptions([{"id": "t9", "title": "News"}], new_feeds)

    assert new_feeds[0]["id"] == "f1"  # stable id across re-import
    assert store.state["cache"]["f1"]["status"] == "ok"
    assert "f2" not in store.state["cache"]  # dropped feed loses cache


def test_clear_stale_pending(tmp_path):
    store = seed(tmp_path)
    store.cache_entry("f2")["status"] = "pending"  # interrupted, no items
    store.clear_stale_pending()
    assert store.state["cache"]["f1"]["status"] == "ok"
    assert store.state["cache"]["f2"]["status"] == "error"
    assert store.state["cache"]["f2"]["error"] == "Refresh interrupted"


def test_save_is_atomic_and_wellformed(tmp_path):
    store = seed(tmp_path)
    store.save()
    import json

    data = json.loads((tmp_path / "s.json").read_text(encoding="utf-8"))
    assert set(data) == {"tabs", "feeds", "cache", "read"}
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".netviber-")]
    assert leftovers == []


def test_mark_read_persists_and_round_trips(tmp_path):
    store = seed(tmp_path)
    marked = store.mark_read(["item1", "item2"])
    assert marked == 2
    store.save()
    reloaded = StateStore(tmp_path / "s.json")
    assert set(reloaded.state["read"]) == {"item1", "item2"}
    assert reloaded.mark_read(["item1"]) == 0  # idempotent


def test_mark_read_prunes_old_entries(tmp_path):
    store = seed(tmp_path)
    old = time.time() - 31 * 24 * 3600
    store.state["read"] = {"stale": old, "fresh": time.time()}
    store.mark_read(["item3"])
    assert "stale" not in store.state["read"]
    assert {"fresh", "item3"} <= set(store.state["read"])


def test_mark_read_capped_at_max_items(tmp_path):
    store = seed(tmp_path)
    now = time.time()
    store.state["read"] = {f"item{i}": now - i for i in range(READ_MAX_ITEMS)}
    store.mark_read(["newitem"])
    assert len(store.state["read"]) == READ_MAX_ITEMS
    assert "item9999" not in store.state["read"]  # oldest dropped
    assert "newitem" in store.state["read"]


def test_save_survives_unwritable_dir(tmp_path):
    # A root-owned data dir (bad install) must not take the app down.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root bypasses directory permissions")
    ro = tmp_path / "ro"
    ro.mkdir()
    store = StateStore(ro / "s.json")
    ro.chmod(0o555)
    try:
        store.mark_read(["k"])
        store.save()  # must not raise
    finally:
        ro.chmod(0o755)
    assert store.state["read"] == {"k": store.state["read"]["k"]}
