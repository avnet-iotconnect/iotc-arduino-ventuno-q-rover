#!/usr/bin/env bash
# Builds dist/ventuno-q-rover.zip with the files a user needs on their PC to set up the rover.
# The folder layout matches the repository, so the README's commands work from the extracted folder.
#
# Usage:
#   bash tools/make_download_zip.sh

set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="ventuno-q-rover"
FILES=(
    LICENSE
    app/app.yaml
    app/python/main.py
    app/python/requirements.txt
    app/python/rover_commands.py
    app/sketch/sketch.ino
    app/sketch/sketch.yaml
    templates/ventuno-rover-template.json
    tools/board_setup.sh
    tools/setup_rover.ps1
    tools/setup_rover.sh
)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/$NAME" "$REPO_DIR/dist"
(cd "$REPO_DIR" && cp --parents "${FILES[@]}" "$STAGE/$NAME/")

OUT="$REPO_DIR/dist/$NAME.zip"
rm -f "$OUT"
(cd "$STAGE" && zip -qrX "$OUT" "$NAME")
echo "$OUT"
unzip -l "$OUT"
