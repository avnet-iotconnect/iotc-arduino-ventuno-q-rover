# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Avnet
# Authors: Zackary Andraka <zackary.andraka@avnet.com> et al.

# Linux side of the rover. Receives /IOTCONNECT commands, turns them into wheel speeds
# (rover_commands.py) and sends those to the sketch over the Bridge. Publishes telemetry
# every 10 seconds.

import json
import queue
import time
from pathlib import Path

from arduino.app_utils import App, Bridge
from avnet.iotconnect.sdk.lite import Callbacks, Client, DeviceConfig
from avnet.iotconnect.sdk.sdklib.mqtt import C2dAck

from rover_commands import CommandError, Rover

# The app folder, mounted at /app in the app's container. tools/board_setup.sh puts the
# credential files here.
APP_DIR = Path(__file__).resolve().parent.parent
DEVICE_CONFIG_PATH = APP_DIR / "iotcDeviceConfig.json"
DEVICE_CERT_PATH = APP_DIR / "device-cert.pem"
DEVICE_KEY_PATH = APP_DIR / "device-pkey.pem"
TRIM_PATH = APP_DIR / "trim.json"

TELEMETRY_PERIOD_S = 10
RECONNECT_PERIOD_S = 30

# Commands arrive on the MQTT client's thread. They are queued and run from the app loop so
# that only one thread calls the Bridge.
commands = queue.Queue()
start_time = time.monotonic()
client = None
last_telemetry = 0.0
last_connect_attempt = 0.0


def drive(wheels, duration_ms):
    if not Bridge.call("drive", *wheels, duration_ms):
        raise CommandError("no motor shield")


def stop():
    Bridge.call("stop")


def status():
    return Bridge.call("status")


def load_trim():
    try:
        trim = json.loads(TRIM_PATH.read_text())
        if len(trim) == 4 and all(50 <= int(t) <= 100 for t in trim):
            return [int(t) for t in trim]
    except (OSError, ValueError, TypeError):
        pass
    return None


rover = Rover(drive, stop, status, load_trim())


def on_command(msg):
    commands.put(msg)


def on_disconnect(reason, disconnected_from_server):
    print(f"/IOTCONNECT disconnected: {reason}", flush=True)
    commands.put(None)


def connect():
    global client
    if client is None:
        if not DEVICE_CONFIG_PATH.exists():
            print(f"Missing {DEVICE_CONFIG_PATH.name}, see the README for the credential files.", flush=True)
            return
        config = DeviceConfig.from_iotc_device_config_json_file(
            device_config_json_path=str(DEVICE_CONFIG_PATH),
            device_cert_path=str(DEVICE_CERT_PATH),
            device_pkey_path=str(DEVICE_KEY_PATH),
        )
        client = Client(config=config, callbacks=Callbacks(command_cb=on_command, disconnected_cb=on_disconnect))
    client.connect()
    if client.is_connected():
        print("/IOTCONNECT connected", flush=True)


def run_command(msg):
    if msg is None:
        # Disconnected: don't leave the rover driving with nobody able to stop it.
        stop()
        return
    print(f"command: {msg.command_name} {msg.command_args}", flush=True)
    trim_before = list(rover.trim)
    try:
        message = rover.handle(msg.command_name, msg.command_args)
        status_code = C2dAck.CMD_SUCCESS_WITH_ACK
    except CommandError as e:
        message = str(e)
        status_code = C2dAck.CMD_FAILED
    if rover.trim != trim_before:
        TRIM_PATH.write_text(json.dumps(rover.trim))
    if msg.ack_id is not None:
        client.send_command_ack(msg, status_code, message)


def send_telemetry():
    fl, fr, rl, rr = rover.trim
    client.send_telemetry({
        "drv": 1 if Bridge.call("is_ready") else 0,
        "cmd": rover.last_command,
        "up": int(time.monotonic() - start_time),
        "tfl": fl,
        "tfr": fr,
        "trl": rl,
        "trr": rr,
    })


def loop():
    global last_telemetry, last_connect_attempt
    now = time.monotonic()
    if (client is None or not client.is_connected()) and (now - last_connect_attempt) >= RECONNECT_PERIOD_S:
        last_connect_attempt = now
        try:
            connect()
        except Exception as e:
            print(f"/IOTCONNECT connect failed: {e}", flush=True)
    try:
        run_command(commands.get(timeout=0.05))
    except queue.Empty:
        pass
    if client is not None and client.is_connected() and (now - last_telemetry) >= TELEMETRY_PERIOD_S:
        last_telemetry = now
        try:
            send_telemetry()
        except Exception as e:
            print(f"telemetry failed: {e}", flush=True)


print("motor shield:", status(), flush=True)
App.run(user_loop=loop)
