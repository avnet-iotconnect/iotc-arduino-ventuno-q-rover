# Developer Guide

This covers how the rover app is built and how to change it.
To run the demo as-is, follow [README.md](README.md) instead.

## Architecture

```
 /IOTCONNECT  <-- MQTT/TLS -->  Linux side (Qualcomm Dragonwing IQ8)        STM32 microcontroller
                                 app/python/main.py                           app/sketch/sketch.ino
                                 /IOTCONNECT Python Lite SDK    -- Bridge -->  Adafruit Motor Shield v2
                                 app/python/rover_commands.py      (RPC)       over I2C, motors M1-M4
```

The rover is one [Arduino App Lab](https://docs.arduino.cc/software/app-lab/) app, installed on the board at
`~/ArduinoApps/rover`. `arduino-app-cli` builds and flashes the sketch, and runs the Python side in a
container with the app folder mounted at `/app`.

| File | Contents |
|---|---|
| `app/app.yaml` | App name and description shown by App Lab |
| `app/sketch/sketch.ino` | Wheel-to-channel map, speed ramp, auto-stop, and the `drive`, `stop`, `status` and `is_ready` Bridge calls |
| `app/sketch/sketch.yaml` | Board platform and the Adafruit libraries the sketch builds with |
| `app/python/main.py` | /IOTCONNECT connection, command queue, telemetry every 10 seconds |
| `app/python/rover_commands.py` | Command parsing, mecanum mixing and trim. It has no /IOTCONNECT or Bridge imports, so it runs on a PC too |
| `app/python/requirements.txt` | Python packages, installed into the app's container on first start |
| `tools/setup_rover.sh` / `.ps1` | Run on the PC. Send `tools/board_setup.sh` and the app to the board over SSH |
| `tools/board_setup.sh` | Runs on the board. Creates the certificate, then builds and starts the app |
| `templates/ventuno-rover-template.json` | /IOTCONNECT device template (code `vqrover`) |

### Motor Control

The Python side turns each command into four wheel speeds (percent, -100 to 100, positive drives the
rover forward) and a duration, and calls `drive` on the sketch.
The sketch ramps each wheel toward its target by 5% every 10 ms, and ramps back to a stop when the
duration ends. The stop happens on the microcontroller, so the rover stops by itself if the Linux side
or the network goes away mid-move.

The sketch's Bridge calls are registered with `provide_safe`, which runs them between `loop()` passes.
The I2C bus and the ramp state are only touched from that one thread.

Mecanum mixing assumes the rollers form an X seen from above:

```
FL = forward + right + rotate        FR = forward - right - rotate
RL = forward - right + rotate        RR = forward + right - rotate
```

All four are scaled down together if any is over 100%. Each wheel's trim then scales its speed.

### Wheel Map

`wheel_map` in `app/sketch/sketch.ino` sets the shield channel and direction of each wheel:

```cpp
    {4, true},  // front-left
    {2, false}, // front-right
    {3, false}, // rear-left
    {1, true},  // rear-right
```

To use a different wiring, or to make the other end of the rover the front, change the channel numbers
and the `is_reversed` flags, then re-run the install step of the setup script.

## Rover Commands

Every command is acknowledged. A rejected command's acknowledgement carries the expected usage.
Speeds are percent of full speed. `ms` is optional, from 1 to 10000, and defaults to 1000.

| Command | Arguments | Action |
|---|---|---|
| `stop` | | Stops all wheels immediately |
| `forward` / `backward` | `speed [ms]`, speed 0-100 | Drives straight |
| `left` / `right` | `speed [ms]`, speed 0-100 | Strafes sideways |
| `rotate-left` / `rotate-right` | `speed [ms]`, speed 0-100 | Rotates in place, counterclockwise / clockwise seen from above |
| `move` | `forward right [ms]`, each -100 to 100 | Any mix of forward and sideways. `move 25 25` drives diagonally |
| `drive` | `forward right rotate [ms]`, each -100 to 100 | Forward, sideways and rotation together |
| `wheels` | `fl fr rl rr [ms]`, each -100 to 100 | Sets each wheel's speed directly. Trim still applies |
| `pairs` | `a b [ms]`, each -100 to 100 | Front-left + rear-right at `a`, front-right + rear-left at `b`, as on the dsPIC33 rover |
| `trim` | `fl fr rl rr`, or `a b` for the two diagonal pairs, each 50-100 | Sets each wheel's speed trim. Saved in `trim.json` in the app folder |
| `diag` | | Returns the motor shield status and each wheel's current duty in the acknowledgement |


## Telemetry

Published every 10 seconds while connected:

| Field | Type | Meaning |
|---|---|---|
| `drv` | INTEGER | Motor shield status (`0` not found on I2C, `1` OK) |
| `cmd` | STRING | The last command the rover accepted |
| `up` | INTEGER | Seconds since the rover app started |
| `tfl` / `tfr` / `trl` / `trr` | INTEGER | Speed trim (%) of each wheel |

The web dashboard's live indicator looks for the `drv` field to find the rover's telemetry messages.

## Working on the Board

With SSH access to the board (`ssh arduino@arduino.local`):

| Task | Command |
|---|---|
| Follow the app's log | `arduino-app-cli app logs user:rover` |
| Stop / start the app | `arduino-app-cli app stop user:rover`, `arduino-app-cli app start user:rover` |
| Check the Wi-Fi connection | `nmcli device status` |

To try a change, edit the files in this repository and re-run the install step of the setup script.
It copies the app over, rebuilds the sketch and restarts the app.
`trim.json` and the device certificate on the board are kept.

`rover_commands.py` can be checked on a PC without the board:

```bash
cd app/python
python3 -c "from rover_commands import mix; print(mix(50, 0, 50))"
```
