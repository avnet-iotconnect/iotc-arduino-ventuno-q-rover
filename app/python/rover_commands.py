# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Avnet
# Authors: Zackary Andraka <zackary.andraka@avnet.com> et al.

# Parses /IOTCONNECT rover commands into per-wheel speeds. Kept free of /IOTCONNECT and
# Bridge imports so it can be tested on a PC.
#
# Wheel order: front-left, front-right, rear-left, rear-right. Speeds are percent, -100 to
# 100, positive = that wheel drives the rover forward.

DEFAULT_DURATION_MS = 1000
MAX_DURATION_MS = 10000
TRIM_MIN = 50
TRIM_MAX = 100


class CommandError(Exception):
    pass


def mix(forward, right, rotate):
    # Mecanum mixing for rollers forming an X seen from above. rotate > 0 turns clockwise
    # seen from above. All wheels scale down together if any would pass 100%, keeping the
    # direction of travel.
    wheels = [
        forward + right + rotate,
        forward - right - rotate,
        forward - right + rotate,
        forward + right - rotate,
    ]
    largest = max(100, *(abs(w) for w in wheels))
    return [round(w * 100 / largest) for w in wheels]


def apply_trim(wheels, trim):
    return [round(w * t / 100) for w, t in zip(wheels, trim)]


def _ints(args, usage):
    try:
        return [int(a) for a in args]
    except (TypeError, ValueError):
        raise CommandError(usage) from None


def _check_range(values, low, high, usage):
    if any(v < low or v > high for v in values):
        raise CommandError(usage)


def _split_duration(values, count, usage):
    # The trailing duration in ms is optional.
    if len(values) == count:
        return values, DEFAULT_DURATION_MS
    if len(values) == count + 1 and 1 <= values[-1] <= MAX_DURATION_MS:
        return values[:-1], values[-1]
    raise CommandError(usage)


# name: (forward, right, rotate) multipliers of the single speed argument
_DIRECTIONS = {
    "forward": (1, 0, 0),
    "backward": (-1, 0, 0),
    "right": (0, 1, 0),
    "left": (0, -1, 0),
    "rotate-right": (0, 0, 1),
    "rotate-left": (0, 0, -1),
}


class Rover:
    # Holds the trim and last command. drive_cb(wheels, duration_ms) and stop_cb() reach the
    # motors, and are the only side effects.
    def __init__(self, drive_cb, stop_cb, status_cb, trim=None):
        self.drive_cb = drive_cb
        self.stop_cb = stop_cb
        self.status_cb = status_cb
        self.trim = list(trim) if trim else [100, 100, 100, 100]
        self.last_command = ""

    def _drive(self, wheels, duration_ms):
        self.drive_cb(apply_trim(wheels, self.trim), duration_ms)

    # Returns the ack message on success, and raises CommandError with the usage on failure.
    def handle(self, name, args):
        args = list(args or [])
        message = self._run(name, args)
        self.last_command = " ".join([name] + [str(a) for a in args])
        return message

    def _run(self, name, args):
        if name == "stop":
            self.stop_cb()
            return "stopped"
        if name == "diag":
            return self.status_cb()
        if name == "trim":
            usage = f"trim <fl> <fr> <rl> <rr>, or <fl+rr> <fr+rl>, each {TRIM_MIN}-{TRIM_MAX}"
            values = _ints(args, usage)
            _check_range(values, TRIM_MIN, TRIM_MAX, usage)
            if len(values) == 2:
                values = [values[0], values[1], values[1], values[0]]
            if len(values) != 4:
                raise CommandError(usage)
            self.trim = values
            return "trim " + " ".join(str(t) for t in self.trim)
        if name in _DIRECTIONS:
            usage = f"{name} <speed 0-100> [ms 1-{MAX_DURATION_MS}]"
            (speed,), duration_ms = _split_duration(_ints(args, usage), 1, usage)
            _check_range([speed], 0, 100, usage)
            forward, right, rotate = (m * speed for m in _DIRECTIONS[name])
            self._drive(mix(forward, right, rotate), duration_ms)
            return "ok"
        if name == "move":
            usage = f"move <forward> <right> [ms], each -100-100, ms 1-{MAX_DURATION_MS}"
            (forward, right), duration_ms = _split_duration(_ints(args, usage), 2, usage)
            _check_range([forward, right], -100, 100, usage)
            self._drive(mix(forward, right, 0), duration_ms)
            return "ok"
        if name == "drive":
            usage = f"drive <forward> <right> <rotate> [ms], each -100-100, ms 1-{MAX_DURATION_MS}"
            values, duration_ms = _split_duration(_ints(args, usage), 3, usage)
            _check_range(values, -100, 100, usage)
            self._drive(mix(*values), duration_ms)
            return "ok"
        if name == "pairs":
            usage = "pairs <fl+rr> <fr+rl> [ms], each -100-100"
            (a, b), duration_ms = _split_duration(_ints(args, usage), 2, usage)
            _check_range([a, b], -100, 100, usage)
            self._drive([a, b, b, a], duration_ms)
            return "ok"
        if name == "wheels":
            usage = "wheels <fl> <fr> <rl> <rr> [ms], each -100-100"
            values, duration_ms = _split_duration(_ints(args, usage), 4, usage)
            _check_range(values, -100, 100, usage)
            self._drive(values, duration_ms)
            return "ok"
        raise CommandError(f"unknown command {name}")
