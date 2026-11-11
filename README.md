# Netviber

A self-hosted, Netvibes-style RSS dashboard. Import an `.opml` file exported
from any feed reader and your feeds show up as headline tiles, grouped into
tabs by their OPML folders.

## Quick start

```sh
uv sync                               # create .venv from pyproject.toml + uv.lock
uv run netviber --opml examples/sample.opml
```

Open http://127.0.0.1:8377 (the browser opens automatically unless you pass
`--no-open`). On first run the app shows an import screen: drop any `.opml`
file on it and you're done.

## Run as a service (Docker + systemd)

The same app runs as a container managed by a user-level systemd unit, so it
starts on boot and survives crashes:

```sh
./deploy/install-service.sh
sudo loginctl enable-linger $USER     # start the unit at boot without login
```

The script checks Docker access, builds the image, installs the user unit and
enables it. The container publishes port 8377 on all interfaces and mounts
`./data` as `/app/data`, so your imported OPML and cached headlines persist.
Manage it with `systemctl --user {status,restart,stop} netviber`; logs are in
the user journal (`journalctl --user -u netviber`) and `docker compose logs`.

Port and container uid are configurable via a `.env` next to `compose.yaml`
(see `.env.example`). Requires Docker access from your user session (e.g.
membership in the `docker` group). The dashboard has no authentication —
exposing it on all interfaces makes it reachable from any device on your LAN.

## Deploy on another host (e.g. a Raspberry Pi)

The image builds natively on arm64 — the base image provides amd64 + arm64 —
so a 64-bit Pi needs nothing special. State is per device: `data/` is
gitignored, so a fresh clone starts at the OPML import screen.

Publish the repo from your machine:

```sh
git push    # origin = git@github.com:LoyVanBeek/netviber.git, branch master
```

On the Pi (64-bit OS, Docker + compose plugin installed):

```sh
git clone git@github.com:LoyVanBeek/netviber.git && cd netviber
cp .env.example .env    # optional, e.g. NETVIBER_PORT if 8377 is taken
./deploy/install-service.sh
sudo loginctl enable-linger $USER
```

Then open `http://<pi-address>:8377` and drop in your OPML file. Updating the
deployment later: `git pull && docker compose up -d --build`.

## Usage

- **Tabs** — one per top-level OPML folder; root-level feeds collect in **Home**
- **Tiles** — favicon, feed title and the latest headlines; click a headline
  to open the article
- **↻ Refresh** — refetches all feeds (per-tile ↻ refreshes just that one)
- **Auto refresh** — pick an interval in the header (off / 5 / 15 / 30 / 60 min)
- **Import OPML…** — swap in a different OPML; cached headlines are kept for
  feeds that appear in both files

## CLI

```sh
python -m netviber [--host H] [--port P] [--data-dir DIR] [--opml FILE] [--no-open]
```

State (subscriptions plus cached headlines) lives in a single JSON file,
`data/netviber.json` by default.

## API

| Route | Description |
| --- | --- |
| `GET /api/state` | Tabs, feeds, cached headlines and per-feed status |
| `POST /api/import` | Upload an OPML file (multipart `file` field) |
| `POST /api/refresh[?feed_id=…]` | Kick off a background refresh |

## How it works

- **Backend** — FastAPI serves the JSON API and the static frontend;
  `feedparser` handles RSS 0.9x/1.0/2.0 and Atom; `httpx` fetches feeds
  concurrently with conditional GETs (`ETag` / `If-Modified-Since`).
- **Frontend** — plain HTML/JS/CSS, no build step.
- Feeds are fetched server-side, so browser CORS limits don't apply.

## Tests

```sh
.venv/bin/python -m pytest
```
