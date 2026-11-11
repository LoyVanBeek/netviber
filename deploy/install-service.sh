#!/usr/bin/env bash
# Install Netviber as a user-level systemd service that runs docker compose.
# Run this ON the target host, from any directory:
#
#   ./deploy/install-service.sh
#
# Steps: sanity-check docker, prepare ./data, build the image, install the
# systemd user unit (with this checkout's absolute path), enable it.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_PATH="$UNIT_DIR/netviber.service"

echo "==> Repo: $REPO_DIR"

# -- sanity checks -----------------------------------------------------------
if ! command -v docker >/dev/null; then
    echo "error: docker is not installed (see https://docs.docker.com/engine/install/)" >&2
    exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
    echo "error: 'docker compose' plugin not available" >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "error: cannot talk to the docker daemon from this user." >&2
    echo "       Add yourself to the 'docker' group and re-login, or use rootless docker." >&2
    exit 1
fi

# -- prepare -----------------------------------------------------------------
# The state dir must exist and be owned by this user BEFORE the first
# `compose up`, or docker creates it root-owned and the container can't write.
mkdir -p "$REPO_DIR/data"

echo "==> Building image (first run pulls the base image; be patient)"
docker compose -f "$REPO_DIR/compose.yaml" build

# -- systemd user unit -------------------------------------------------------
mkdir -p "$UNIT_DIR"
cat > "$UNIT_PATH" <<UNIT
[Unit]
Description=Netviber RSS dashboard (docker compose)
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/docker compose -f $REPO_DIR/compose.yaml up -d --wait
ExecStop=/usr/bin/docker compose -f $REPO_DIR/compose.yaml down

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now netviber

# -- linger hint -------------------------------------------------------------
if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]]; then
    echo
    echo "==> One step left (needs sudo): start the service at boot without login"
    echo "    sudo loginctl enable-linger $USER"
fi

echo
systemctl --user --no-pager --lines=0 status netviber || true
echo "==> Done. Dashboard: http://localhost:${NETVIBER_PORT:-8377}/"
