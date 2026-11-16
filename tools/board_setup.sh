#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Avnet
# Authors: Zackary Andraka <zackary.andraka@avnet.com> et al.
#
# Runs on the VENTUNO Q, sent over SSH by setup_rover.sh / setup_rover.ps1 on the PC.
#
#   board_setup.sh cert <unique-id>   create the device key and certificate, and print the certificate
#   board_setup.sh install            unpack /tmp/rover-setup.tgz into the rover app, then build, start
#                                     and autostart the app

set -e

APP_DIR="$HOME/ArduinoApps/rover"
BUNDLE=/tmp/rover-setup.tgz

make_cert() {
    local duid="$1"
    mkdir -p "$APP_DIR"
    cd "$APP_DIR"
    if [ -f device-cert.pem ] && [ -f device-pkey.pem ] &&
        openssl x509 -in device-cert.pem -noout -subject | grep -q "CN *= *$duid\$"; then
        echo "Using the existing certificate for $duid."
    else
        openssl ecparam -name prime256v1 -genkey -noout -out device-pkey.pem
        chmod 600 device-pkey.pem
        openssl req -new -x509 -days 36500 -key device-pkey.pem -subj "/CN=$duid" -out device-cert.pem
        echo "Created a new certificate for $duid."
    fi
    echo
    cat device-cert.pem
}

install_app() {
    mkdir -p "$APP_DIR"
    tar -xzf "$BUNDLE" -C "$APP_DIR"
    rm -f "$BUNDLE"
    if [ ! -f "$APP_DIR/device-cert.pem" ] || [ ! -f "$APP_DIR/device-pkey.pem" ]; then
        echo "No device certificate on the board. Run the cert step first." >&2
        exit 1
    fi
    # Older board images ship an Arduino Zephyr core that doesn't know the VENTUNO Q yet.
    if ! arduino-cli board details -b arduino:zephyr:ventunoq >/dev/null 2>&1; then
        echo "Updating the Arduino Zephyr core..."
        arduino-cli core update-index >/dev/null
        arduino-cli core install arduino:zephyr >/dev/null
    fi
    echo "Building and starting the rover app. The first build takes a few minutes..."
    arduino-app-cli app stop user:rover >/dev/null 2>&1 || true
    if ! arduino-app-cli app start user:rover > /tmp/rover-build.log 2>&1; then
        grep -i -E "error" /tmp/rover-build.log | tail -20 >&2
        echo "Build failed. The full log is in /tmp/rover-build.log on the board." >&2
        exit 1
    fi
    arduino-app-cli properties set default user:rover >/dev/null
    echo "The rover app starts by itself whenever the board boots."
    # "app logs" follows the log, so each check is cut off after a few seconds.
    for _ in $(seq 20); do
        if timeout 5 arduino-app-cli app logs user:rover 2>/dev/null | grep -q "/IOTCONNECT connected"; then
            break
        fi
    done
    echo
    timeout 5 arduino-app-cli app logs user:rover 2>/dev/null | grep -E "motor shield|/IOTCONNECT|Missing" | tail -4 || true
}

case "$1" in
    cert)
        if ! [[ "$2" =~ ^[A-Za-z0-9-]+$ ]]; then
            echo "The Unique ID can only contain letters, numbers and hyphens." >&2
            exit 1
        fi
        make_cert "$2"
        ;;
    install)
        install_app
        ;;
    *)
        echo "usage: board_setup.sh cert <unique-id> | install" >&2
        exit 1
        ;;
esac
