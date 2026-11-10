"""OPML subscription-list parsing.

In an OPML file, ``<outline>`` elements with an ``xmlUrl`` attribute are
feeds; plain outlines are folders.  Top-level folders become dashboard tabs,
deeper folders flatten into their top-level ancestor's tab, and feeds at the
root of the document collect in a trailing catch-all tab ("Home" -- "Feeds"
when the file has no folders at all).
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from .state import new_id

log = logging.getLogger("netviber.opml")


class OpmlError(ValueError):
    """Raised when the uploaded file is not a usable OPML document."""


def parse_opml(data: bytes | str) -> tuple[list[dict], list[dict]]:
    """Parse OPML into ``(tabs, feeds)``.

    tabs  -- [{"id": ..., "title": ...}] in document order
    feeds -- [{"id", "title", "xml_url", "html_url", "tab_id"}]
    """
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise OpmlError(f"Not valid XML: {exc}") from exc

    body = root.find("body") if root.tag.lower() == "opml" else None
    if body is None:
        raise OpmlError("That doesn't look like an OPML file (missing <opml><body>).")

    tabs: list[dict] = []
    feeds: list[dict] = []
    seen: set[str] = set()
    _walk(body, None, tabs, feeds, seen)

    orphans = [feed for feed in feeds if feed["tab_id"] is None]
    if orphans:
        tab = {"id": new_id("tab"), "title": "Home" if tabs else "Feeds"}
        tabs.append(tab)
        for feed in orphans:
            feed["tab_id"] = tab["id"]

    if not feeds:
        raise OpmlError("No feeds found in that OPML file.")
    return tabs, feeds


def _walk(
    container: ET.Element,
    tab_id: str | None,
    tabs: list[dict],
    feeds: list[dict],
    seen: set[str],
) -> None:
    for outline in container.findall("outline"):
        xml_url = _attr(outline, "xmlUrl").strip()
        if xml_url:
            if _is_http(xml_url) and xml_url not in seen:
                seen.add(xml_url)
                feeds.append(
                    {
                        "id": new_id("feed"),
                        "title": _attr(outline, "text", "title").strip()
                        or _host_of(xml_url)
                        or xml_url,
                        "xml_url": xml_url,
                        "html_url": _attr(outline, "htmlUrl").strip(),
                        "tab_id": tab_id,
                    }
                )
            # Feeds occasionally nest extra outlines below them; flatten those
            # into the same tab instead of dropping them.
            _walk(outline, tab_id, tabs, feeds, seen)
        else:
            title = _attr(outline, "text", "title").strip() or "Untitled"
            if tab_id is None:
                tab = {"id": new_id("tab"), "title": title}
                tabs.append(tab)
                _walk(outline, tab["id"], tabs, feeds, seen)
            else:
                log.debug("Flattening nested folder %r into tab %s", title, tab_id)
                _walk(outline, tab_id, tabs, feeds, seen)


def _attr(outline: ET.Element, *names: str) -> str:
    """First present attribute, tolerant of case variants (xmlUrl/xmlurl)."""
    for name in names:
        value = outline.get(name)
        if value:
            return value
    lowered = {k.lower(): v for k, v in outline.attrib.items() if v}
    for name in names:
        value = lowered.get(name.lower())
        if value:
            return value
    return ""


def _is_http(url: str) -> bool:
    return url.lower().startswith(("http://", "https://"))


def _host_of(url: str) -> str:
    host = urlsplit(url).netloc
    return host[4:] if host.startswith("www.") else host
