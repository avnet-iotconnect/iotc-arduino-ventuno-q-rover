#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Avnet
# Authors: Zackary Andraka <zackary.andraka@avnet.com> et al.
#
# Sets up the rover app on a VENTUNO Q over SSH, from Linux or macOS. See the README.
#
#   setup_rover.sh cert --duid MYUNIQUEID [--board HOST] [--user USER]
#   setup_rover.sh install --config iotcDeviceConfig.json [--board HOST] [--user USER]

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BOARD="arduino.local"
BOARD_USER="arduino"
DUID=""
CONFIG=""

usage() {
    sed -n '8,9p' "${BASH_SOURCE[0]}" | sed 's/^# //' >&2
    exit 1
}

STEP="$1"
[ -n "$STEP" ] || usage
shift
while [ $# -gt 0 ]; do
    case "$1" in
        --board) BOARD="$2"; shift 2 ;;
        --user) BOARD_USER="$2"; shift 2 ;;
        --duid) DUID="$2"; shift 2 ;;
        --config) CONFIG="$2"; shift 2 ;;
        *) usage ;;
    esac
done

TARGET="$BOARD_USER@$BOARD"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new)

case "$STEP" in
    cert)
        [ -n "$DUID" ] || usage
        if ! [[ "$DUID" =~ ^[A-Za-z0-9-]+$ ]]; then
            echo "The Unique ID can only contain letters, numbers and hyphens." >&2
            exit 1
        fi
        echo "Connecting to $TARGET. Enter the board's password if asked."
        ssh "${SSH_OPTS[@]}" "$TARGET" "bash -s -- cert '$DUID'" < "$REPO_DIR/tools/board_setup.sh"
        ;;
    install)
        [ -n "$CONFIG" ] || usage
        if [ ! -f "$CONFIG" ]; then
            echo "Can't find $CONFIG" >&2
            exit 1
        fi
        STAGE=$(mktemp -d)
        trap 'rm -rf "$STAGE"' EXIT
        mkdir "$STAGE/rover"
        cp -r "$REPO_DIR/app/app.yaml" "$REPO_DIR/app/sketch" "$REPO_DIR/app/python" "$STAGE/rover/"
        rm -rf "$STAGE/rover/python/__pycache__"
        cp "$CONFIG" "$STAGE/rover/iotcDeviceConfig.json"
        # COPYFILE_DISABLE keeps macOS tar from adding ._ metadata files.
        COPYFILE_DISABLE=1 tar -czf "$STAGE/rover-setup.tgz" -C "$STAGE/rover" .
        echo "Copying the rover app to $TARGET. Enter the board's password if asked."
        scp "${SSH_OPTS[@]}" -q "$STAGE/rover-setup.tgz" "$TARGET:/tmp/rover-setup.tgz"
        echo "Installing. Enter the board's password again if asked."
        ssh "${SSH_OPTS[@]}" "$TARGET" "bash -s -- install" < "$REPO_DIR/tools/board_setup.sh"
        ;;
    *)
        usage
        ;;
esac
