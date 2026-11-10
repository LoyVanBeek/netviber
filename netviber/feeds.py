"""Feed fetching and parsing, with per-feed conditional-GET caching."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from .state import StateStore

log = logging.getLogger("netviber.feeds")

USER_AGENT = "Netviber/1.0 (RSS dashboard)"
FETCH_TIMEOUT = httpx.Timeout(10.0)
CONCURRENCY = 8
MAX_ITEMS = 40  # stored per feed; the UI paginates tiles at 10 per page

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_feed(content: bytes) -> list[dict]:
    """Parse RSS/Atom content into plain headline dicts."""
    parsed = feedparser.parse(content)
    items = []
    for entry in parsed.entries[:MAX_ITEMS]:
        title = _clean(str(entry.get("title", "")))
        link = str(entry.get("link", "") or "")
        if not title and not link:
            continue
        items.append(
            {
                "id": str(entry.get("id", "") or link or title),
                "title": title or "(untitled)",
                "link": link,
                "published": _entry_date(entry),
            }
        )
    return items


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", _TAG_RE.sub("", text)).strip()


def _entry_date(entry: object) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        tm = entry.get(key)  # type: ignore[attr-defined]
        if tm:
            try:
                return datetime(*tm[:6], tzinfo=timezone.utc).isoformat(
                    timespec="seconds"
                )
            except (TypeError, ValueError):
                pass
    for key in ("published", "updated"):
        raw = entry.get(key)  # type: ignore[attr-defined]
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat(
                    timespec="seconds"
                )
            except (TypeError, ValueError):
                pass
    return None


def _short(exc: Exception) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:120]


class FeedFetcher:
    """Refreshes feeds concurrently in the background, updating the store."""

    def __init__(self, store: StateStore) -> None:
        self.store = store
        self._global_task: asyncio.Task | None = None
        self._single_tasks: set[asyncio.Task] = set()

    def refresh_all(self, feed_ids: list[str] | None = None) -> bool:
        """Start refreshing feeds in the background.

        Returns False when a full refresh is already running; per-feed
        refreshes are always allowed.
        """
        if feed_ids is None and self._global_task and not self._global_task.done():
            return False
        ids = (
            list(feed_ids)
            if feed_ids is not None
            else [f["id"] for f in self.store.state["feeds"]]
        )
        self._mark_pending(ids)
        task = asyncio.create_task(self._run(ids))
        if feed_ids is None:
            self._global_task = task
        else:
            self._track(task)
        return True

    def refresh_one(self, feed_id: str) -> bool:
        return self.refresh_all([feed_id])

    # -- internals ---------------------------------------------------------

    def _track(self, task: asyncio.Task) -> None:
        self._single_tasks.add(task)
        task.add_done_callback(self._single_tasks.discard)

    def _mark_pending(self, ids: list[str]) -> None:
        for fid in ids:
            entry = self.store.cache_entry(fid)
            entry["status"] = "pending"
            entry["error"] = None
        self.store.save()

    async def _run(self, ids: list[str]) -> None:
        sem = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            await asyncio.gather(*(self._fetch(client, fid, sem) for fid in ids))
        self.store.save()

    async def _fetch(
        self, client: httpx.AsyncClient, feed_id: str, sem: asyncio.Semaphore
    ) -> None:
        feed = self.store.get_feed(feed_id)
        if feed is None:
            return
        entry = self.store.cache_entry(feed_id)
        url = feed["xml_url"]
        async with sem:
            try:
                headers = {}
                if entry.get("etag"):
                    headers["If-None-Match"] = entry["etag"]
                if entry.get("modified"):
                    headers["If-Modified-Since"] = entry["modified"]
                resp = await client.get(url, headers=headers)
                if resp.status_code == 304:  # not modified; cache is still good
                    entry.update(status="ok", error=None, fetched_at=now_iso())
                    return
                resp.raise_for_status()
                entry.update(
                    status="ok",
                    error=None,
                    fetched_at=now_iso(),
                    etag=resp.headers.get("etag"),
                    modified=resp.headers.get("last-modified"),
                    items=parse_feed(resp.content),
                )
            except Exception as exc:  # surface the failure on the tile
                log.warning("Feed %s (%s) failed: %s", feed.get("title"), url, exc)
                entry.update(status="error", error=_short(exc), fetched_at=now_iso())
            finally:
                self.store.save()
