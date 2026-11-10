"""Tests for state persistence and re-import cache carry-over."""

from netviber.state import StateStore

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
    assert set(data) == {"tabs", "feeds", "cache"}
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".netviber-")]
    assert leftovers == []
