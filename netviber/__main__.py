"""Command-line entry point: ``python -m netviber``."""

from __future__ import annotations

import argparse
import logging
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="netviber",
        description="A Netvibes-style RSS dashboard fed by an OPML file.",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8377,
                        help="Port (default: 8377)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="Where state is stored (default: <project>/data)")
    parser.add_argument("--opml", type=Path, default=None,
                        help="OPML file to import on first run")
    parser.add_argument("--no-open", action="store_true",
                        help="Do not open a browser window")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    app = create_app(data_dir=args.data_dir, opml_file=args.opml)

    url = f"http://{args.host}:{args.port}"
    if not args.no_open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    logging.getLogger("netviber").info("Netviber listening on %s", url)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
