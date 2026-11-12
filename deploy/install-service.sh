#!/usr/bin/env bash
# Install Netviber as a system-level systemd service that runs docker compose.
# Run from the repo checkout:
#
#   sudo ./deploy/install-service.sh
#
# Steps: sanity-check docker, prepare ./data, build the image, install the
# systemd unit (with this checkout's absolute path), enable it. No linger
# needed: system units start at boot regardless of who is logged in.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_PATH=/etc/systemd/system/netviber.service
CONTAINER_UID="${UID:-1000}"

echo "==> Repo: $REPO_DIR"

if [[ $EUID -ne 0 ]]; then
    echo "error: run with sudo: sudo ./deploy/install-service.sh" >&2
    exit 1
fi
if ! command -v docker >/dev/null; then
    echo "error: docker is not installed (see https://docs.docker.com/engine/install/)" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "error: 'docker compose' plugin not available" >&2
    exit 1
fi

# -- prepare -----------------------------------------------------------------
# The state dir must exist and be owned by the container user BEFORE the first
# `compose up`, or docker creates it root-owned and the container can't write.
mkdir -p "$REPO_DIR/data"
chown "$CONTAINER_UID:$CONTAINER_UID" "$REPO_DIR/data"

echo "==> Building image (first run pulls the base image; be patient)"
docker compose -f "$REPO_DIR/compose.yaml" build

# -- systemd unit ------------------------------------------------------------
sed "s|@REPO_DIR@|$REPO_DIR|g" "$REPO_DIR/deploy/netviber.service" > "$UNIT_PATH"
systemctl daemon-reload
systemctl enable --now netviber

systemctl --no-pager --lines=0 status netviber || true
echo "==> Done. Dashboard: http://localhost:${NETVIBER_PORT:-8377}/"
echo "    Manage it with:  systemctl {status,restart,stop} netviber"
