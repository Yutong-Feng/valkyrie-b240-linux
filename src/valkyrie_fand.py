#!/usr/bin/env python3
"""Applies fixed speeds / temperature curves from the cooler config to the pump and fan headers."""
import argparse
import signal
import sys
import time

import cooler_common as cc

INTERVAL = 1.0
RAMP_DOWN_PER_SEC = 2.0
MAX_TEMP_FAILURES = 3


def log(msg):
    print(msg, flush=True)


def restore_all():
    for name in cc.CHANNEL_LABEL:
        try:
            cc.FanChannel(name).set_auto()
        except OSError as e:
            log(f"restore {name}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true", help="hand all headers back to BIOS control and exit")
    if ap.parse_args().restore:
        restore_all()
        return

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    chans = {n: cc.FanChannel(n) for n in cc.CHANNEL_LABEL}
    applied = {n: None for n in chans}  # None = left to BIOS
    cfg, mtime = cc.load_config(), cc.config_mtime()
    temp_failures = {s: 0 for s in cc.TEMP_SOURCES}
    warned_perm = False
    try:
        while True:
            if cc.config_mtime() != mtime:
                cfg, mtime = cc.load_config(), cc.config_mtime()
                log("config reloaded")
            fcfg = cfg["fan"]
            temps = {}
            for src in {cc.channel_temp_source(fcfg, n) for n in chans}:
                temps[src] = cc.read_temp(src)
                temp_failures[src] = 0 if temps[src] is not None else temp_failures[src] + 1
                if temp_failures[src] == MAX_TEMP_FAILURES:
                    log(f"temperature '{src}' unavailable, handing its fans back to BIOS")
            cpu = cc.read_temp("tctl")

            for name, ch in chans.items():
                ccfg = fcfg["channels"][name]
                src = cc.channel_temp_source(fcfg, name)
                t = temps[src]
                if ccfg["mode"] == "bios" or temp_failures[src] >= MAX_TEMP_FAILURES:
                    if applied[name] is not None:
                        try:
                            ch.set_auto()
                            log(f"{name}: BIOS auto")
                        except OSError as e:
                            log(f"{name}: set auto failed: {e}")
                        applied[name] = None
                    continue
                if not ch.writable():
                    if not warned_perm:
                        log("pwm files not writable; run the one-time authorization from the GUI")
                        warned_perm = True
                    continue
                warned_perm = False

                prev = applied[name]
                if prev is None:
                    prev = ch.pwm_percent() or 100
                if ccfg["mode"] == "fixed":
                    target = ccfg["fixed"]
                elif t is not None:
                    target = cc.curve_value(ccfg["curve"], t)
                else:
                    target = prev
                if any(x is not None and x >= fcfg["emergency_temp"] for x in (t, cpu)):
                    target = 100
                target = max(target, cc.FLOOR[name])
                if target < prev:
                    target = max(target, prev - RAMP_DOWN_PER_SEC * INTERVAL)

                try:
                    if applied[name] is None or round(target) != round(applied[name]):
                        ch.set_percent(target)
                    applied[name] = target
                except OSError as e:
                    log(f"{name}: write failed: {e}")
            time.sleep(INTERVAL)
    finally:
        for name, ch in chans.items():
            if applied[name] is not None:
                try:
                    ch.set_auto()
                except OSError:
                    pass


if __name__ == "__main__":
    main()
