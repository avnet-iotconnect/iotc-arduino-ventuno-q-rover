/* SPDX-License-Identifier: MIT
 * Copyright (C) 2026 Avnet
 * Authors: Zackary Andraka <zackary.andraka@avnet.com> et al.
 */

// Wheel control on the STM32 side of the VENTUNO Q, through the Adafruit Motor Shield v2.
// The Linux side (python/main.py) calls "drive" and "stop" over the Bridge. Each drive has a
// duration, and the wheels ramp to a stop when it ends, so the rover stops on its own if
// Linux or the cloud connection goes away mid-move.

#include <Wire.h>
#include <Adafruit_MotorShield.h>
#include "Arduino_RouterBridge.h"

#define WHEEL_COUNT 4
#define MAX_DURATION_MS 10000UL
#define UPDATE_PERIOD_MS 10UL
// Duty changes by at most RAMP_STEP percent every UPDATE_PERIOD_MS, so 0 to 100% takes 200 ms.
// This keeps the four motors' start-up surge under the 2.8 A limit of the board's 5V header pin.
#define RAMP_STEP 5

// Wheel order everywhere in this app: front-left, front-right, rear-left, rear-right.
// Channel (M1-M4) and direction come from the wheel direction test.
static const struct {
    int channel;
    bool is_reversed;
} wheel_map[WHEEL_COUNT] = {
    {4, false}, // front-left
    {2, true},  // front-right
    {3, true},  // rear-left
    {1, false}, // rear-right
};

static Adafruit_MotorShield shield;
static Adafruit_DCMotor *motors[WHEEL_COUNT];
static bool is_shield_ready = false;
static int target_percent[WHEEL_COUNT];
static int actual_percent[WHEEL_COUNT];
static bool is_moving = false;
static unsigned long stop_at_ms;
static unsigned long last_update_ms;

static int clamp(int value, int min, int max) {
    return (value < min) ? min : ((value > max) ? max : value);
}

static void apply(int wheel, int percent) {
    Adafruit_DCMotor *motor = motors[wheel];
    if (wheel_map[wheel].is_reversed) {
        percent = -percent;
    }
    if (percent == 0) {
        motor->run(RELEASE);
        return;
    }
    motor->setSpeed((abs(percent) * 255 + 50) / 100);
    motor->run((percent > 0) ? FORWARD : BACKWARD);
}

// Speeds are percent, -100 to 100, positive = the wheel drives the rover forward.
static bool drive(int fl, int fr, int rl, int rr, int duration_ms) {
    if (!is_shield_ready) {
        return false;
    }
    const int requested[WHEEL_COUNT] = {fl, fr, rl, rr};
    for (int w = 0; w < WHEEL_COUNT; w++) {
        target_percent[w] = clamp(requested[w], -100, 100);
    }
    stop_at_ms = millis() + (unsigned long)clamp(duration_ms, 1, MAX_DURATION_MS);
    is_moving = true;
    return true;
}

// Stops immediately, without ramping down.
static void stop() {
    is_moving = false;
    for (int w = 0; w < WHEEL_COUNT; w++) {
        target_percent[w] = 0;
        actual_percent[w] = 0;
        if (is_shield_ready) {
            apply(w, 0);
        }
    }
}

static String status() {
    if (!is_shield_ready) {
        return "no motor shield";
    }
    String text = "shield ok, duty";
    for (int w = 0; w < WHEEL_COUNT; w++) {
        text += " " + String(actual_percent[w]);
    }
    return text;
}

static bool is_ready() {
    return is_shield_ready;
}

void setup() {
    Bridge.begin();
    is_shield_ready = shield.begin();
    for (int w = 0; w < WHEEL_COUNT; w++) {
        motors[w] = shield.getMotor(wheel_map[w].channel);
    }
    stop();
    // provide_safe runs the handlers between loop() passes, so the I2C bus and the ramp
    // state are only ever touched from one thread.
    Bridge.provide_safe("drive", drive);
    Bridge.provide_safe("stop", stop);
    Bridge.provide_safe("status", status);
    Bridge.provide_safe("is_ready", is_ready);
}

void loop() {
    unsigned long now = millis();
    if ((now - last_update_ms) < UPDATE_PERIOD_MS) {
        return;
    }
    last_update_ms = now;
    if (is_moving && ((long)(now - stop_at_ms) >= 0)) {
        is_moving = false;
        for (int w = 0; w < WHEEL_COUNT; w++) {
            target_percent[w] = 0;
        }
    }
    for (int w = 0; w < WHEEL_COUNT; w++) {
        int step = clamp(target_percent[w] - actual_percent[w], -RAMP_STEP, RAMP_STEP);
        if (step != 0) {
            actual_percent[w] += step;
            apply(w, actual_percent[w]);
        }
    }
}
