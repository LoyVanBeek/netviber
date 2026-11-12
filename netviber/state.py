"""App state persistence: one JSON file, written atomically.

The whole application state -- tabs, feeds and the headline cache -- lives
in a single JSON document.  Writes go to a temp file and are ``os.replace``d
into place, so a crash can never leave a half-written state file behind.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("netviber.state")

READ_MAX_AGE = 30 * 24 * 3600  # forget read marks older than 30 days
READ_MAX_ITEMS = 10000  # newest read marks win


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


class StateStore:
    """In-memory state dict backed by an atomically-written JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state: dict[str, Any] = self._load()

    # -- reading ---------------------------------------------------------

    @property
    def state(self) -> dict[str, Any]:
        return self._state

    def snapshot(self) -> dict[str, Any]:
        """Deep copy, safe to serialize while background tasks mutate state."""
        return copy.deepcopy(self._state)

    def get_feed(self, feed_id: str) -> dict[str, Any] | None:
        for feed in self._state["feeds"]:
            if feed["id"] == feed_id:
                return feed
        return None

    def cache_entry(self, feed_id: str) -> dict[str, Any]:
        """Get (or create) the cache entry for a feed."""
        cache = self._state["cache"]
        entry = cache.get(feed_id)
        if entry is None:
            entry = {
                "status": "pending",
                "error": None,
                "fetched_at": None,
                "etag": None,
                "modified": None,
                "items": [],
            }
            cache[feed_id] = entry
        return entry

    # -- writing ---------------------------------------------------------

    def replace_subscriptions(self, tabs: list[dict], feeds: list[dict]) -> None:
        """Swap in a new tab/feed layout, reusing ids and cache for known urls.

        Feeds whose ``xml_url`` existed before keep their id, so their cached
        headlines survive a re-import.  Feeds dropped from the OPML lose
        their cache.
        """
        prev_by_url = {f["xml_url"]: f for f in self._state["feeds"]}
        for feed in feeds:
            prev = prev_by_url.get(feed["xml_url"])
            if prev is not None:
                feed["id"] = prev["id"]
        self._state["tabs"] = tabs
        self._state["feeds"] = feeds
        keep = {f["id"] for f in feeds}
        self._state["cache"] = {
            fid: entry for fid, entry in self._state["cache"].items() if fid in keep
        }

    def mark_read(self, keys: list[str]) -> int:
        """Record read marks (key -> timestamp). Read-only: no unmarking.

        Returns the number of previously-unread keys that were marked.
        """
        read = self._state["read"]
        marked = 0
        for key in keys:
            if key not in read:
                marked += 1
            read[key] = int(time.time())
        self._prune_read()
        return marked

    def _prune_read(self) -> None:
        read = self._state["read"]
        cutoff = int(time.time()) - READ_MAX_AGE
        if any(ts < cutoff for ts in read.values()):
            for key in [k for k, ts in read.items() if ts < cutoff]:
                del read[key]
        if len(read) > READ_MAX_ITEMS:
            newest = sorted(read.items(), key=lambda kv: kv[1], reverse=True)
            self._state["read"] = dict(newest[:READ_MAX_ITEMS])

    def clear_stale_pending(self) -> None:
        """Resolve entries stuck as "pending" (e.g. after a crash mid-refresh)."""
        for entry in self._state["cache"].values():
            if entry.get("status") == "pending":
                if entry.get("items"):
                    entry["status"] = "ok"
                else:
                    entry["status"] = "error"
                    entry["error"] = "Refresh interrupted"

    def save(self) -> None:
        fd, tmp = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".netviber-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._state, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            log.exception("Failed to write state file %s", self.path)
            try:
                os.unlink(tmp)
            except OSError:
                pass

    # -- internals ---------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        empty: dict[str, Any] = {"tabs": [], "feeds": [], "cache": {}, "read": {}}
        if not self.path.exists():
            return empty
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("Could not read %s; starting with empty state", self.path)
            return empty
        if not isinstance(data, dict):
            return empty
        for key in ("tabs", "feeds", "cache"):
            if isinstance(data.get(key), (list, dict)):
                empty[key] = data[key]
        read = data.get("read")
        if isinstance(read, dict):
            clean = {
                k: int(v)
                for k, v in read.items()
                if isinstance(v, (int, float))
            }
            empty["read"] = clean
            self._state = empty
            self._prune_read()
            return self._state
        return empty
