"""FastAPI application: JSON API + static frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from . import opml
from .feeds import FeedFetcher
from .state import StateStore

log = logging.getLogger("netviber.app")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = PROJECT_ROOT / "static"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"


def create_app(data_dir: Path | None = None, opml_file: Path | None = None) -> FastAPI:
    data_dir = data_dir or DEFAULT_DATA_DIR
    store = StateStore(data_dir / "netviber.json")
    fetcher = FeedFetcher(store)

    if opml_file is not None:
        if store.state["feeds"]:
            log.info("State already has feeds; ignoring --opml %s", opml_file)
        else:
            try:
                tabs, feeds = opml.parse_opml(opml_file.read_bytes())
            except (OSError, opml.OpmlError) as exc:
                log.error("Could not import %s: %s", opml_file, exc)
            else:
                store.replace_subscriptions(tabs, feeds)
                store.save()
                log.info("Imported %d feeds in %d tabs from %s",
                         len(feeds), len(tabs), opml_file)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        store.clear_stale_pending()
        store.save()
        if store.state["feeds"] and any(
            f["id"] not in store.state["cache"] for f in store.state["feeds"]
        ):
            log.info("Fetching feeds for the first time")
            fetcher.refresh_all()
        yield

    app = FastAPI(title="Netviber", version="1.0.0", lifespan=lifespan)
    app.state.store = store

    @app.get("/api/state")
    async def get_state():
        return store.snapshot()

    @app.get("/api/read")
    async def get_read():
        return {"read": store.state["read"]}

    @app.post("/api/read")
    async def mark_read(keys: list[str] = Body(..., min_length=1)):
        """Mark items read (key -> timestamp). Read-only: no unmarking."""
        marked = store.mark_read(keys)
        store.save()
        return {"marked": marked}

    @app.post("/api/import")
    async def import_opml(file: UploadFile):
        raw = await file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty file")
        try:
            tabs, feeds = opml.parse_opml(raw)
        except opml.OpmlError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        store.replace_subscriptions(tabs, feeds)
        store.save()
        log.info("Imported %d feeds in %d tabs from %s",
                 len(feeds), len(tabs), file.filename)
        fetcher.refresh_all()
        return store.snapshot()

    @app.post("/api/refresh")
    async def refresh(feed_id: str | None = None):
        if feed_id:
            if store.get_feed(feed_id) is None:
                raise HTTPException(status_code=404, detail="Unknown feed")
            started = fetcher.refresh_one(feed_id)
        else:
            started = fetcher.refresh_all()
        return {"started": started}

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
