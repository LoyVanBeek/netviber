"""Tests for OPML parsing."""

import pytest

from netviber.opml import OpmlError, parse_opml

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
<head><title>Subscriptions</title></head>
<body>
  <outline text="News">
    <outline text="BBC News" xmlUrl="http://feeds.bbci.co.uk/news/rss.xml"
             htmlUrl="https://www.bbc.co.uk/news"/>
    <outline text="World">
      <outline text="NPR" xmlUrl="https://feeds.npr.org/1001/rss.xml"/>
    </outline>
  </outline>
  <outline text="Tech">
    <outline text="HN" xmlUrl="https://hnrss.org/frontpage"/>
  </outline>
  <outline text="xkcd" xmlUrl="https://xkcd.com/rss.xml"/>
  <outline text="Dup" xmlUrl="http://feeds.bbci.co.uk/news/rss.xml"/>
  <outline text="Junk" xmlUrl="ftp://not-a-web-feed"/>
</body>
</opml>
"""

FLAT = """<?xml version="1.0"?>
<opml version="2.0"><body>
  <outline text="A" xmlUrl="http://a/rss"/>
  <outline text="B" xmlUrl="http://b/rss"/>
</body></opml>
"""


def titles(feeds):
    return [f["title"] for f in feeds]


def test_folders_become_tabs_in_order():
    tabs, feeds = parse_opml(SAMPLE)
    assert [t["title"] for t in tabs] == ["News", "Tech", "Home"]


def test_root_feed_goes_to_catchall_home_tab():
    tabs, feeds = parse_opml(SAMPLE)
    home = tabs[-1]
    assert home["title"] == "Home"
    xkcd = [f for f in feeds if f["title"] == "xkcd"][0]
    assert xkcd["tab_id"] == home["id"]


def test_nested_folder_flattens_into_top_level_tab():
    tabs, feeds = parse_opml(SAMPLE)
    news = tabs[0]
    npr = [f for f in feeds if f["title"] == "NPR"][0]
    assert npr["tab_id"] == news["id"]


def test_duplicate_urls_are_deduped():
    _, feeds = parse_opml(SAMPLE)
    assert titles(feeds).count("BBC News") == 1
    assert "Dup" not in titles(feeds)


def test_non_http_urls_are_skipped():
    _, feeds = parse_opml(SAMPLE)
    assert "Junk" not in titles(feeds)


def test_flat_opml_gets_single_feeds_tab():
    tabs, feeds = parse_opml(FLAT)
    assert [t["title"] for t in tabs] == ["Feeds"]
    assert all(f["tab_id"] == tabs[0]["id"] for f in feeds)
    assert len(feeds) == 2


def test_feed_without_title_falls_back_to_host():
    opml = '<opml><body><outline xmlUrl="http://example.com/feed"/></body></opml>'
    _, feeds = parse_opml(opml)
    assert feeds[0]["title"] == "example.com"


def test_lowercase_xmlurl_accepted():
    opml = '<opml><body><outline text="X" xmlurl="http://x/feed"/></body></opml>'
    _, feeds = parse_opml(opml)
    assert feeds[0]["xml_url"] == "http://x/feed"


def test_invalid_xml_raises():
    with pytest.raises(OpmlError):
        parse_opml("<opml><body>")


def test_not_opml_raises():
    with pytest.raises(OpmlError):
        parse_opml("<html><body><p>hi</p></body></html>")


def test_no_feeds_raises():
    with pytest.raises(OpmlError):
        parse_opml('<opml><body><outline text="Empty folder"/></body></opml>')
