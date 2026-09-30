#!/usr/bin/env python3
"""System monitor for the Valkyrie B240 cooler LCD (MS912C, 320x240 panel)."""
import argparse
import datetime
import errno
import signal
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import cooler_common as cc

PANEL_W, PANEL_H = 320, 240
# The cooler samples the top 640x240 of a 640x480 signal and halves it horizontally.
OUT_W, OUT_H = 640, 480

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
BG = (12, 14, 20)
FG = (230, 233, 240)
DIM = (130, 138, 155)
TRACK = (40, 44, 56)


def font(size, bold=True):
    return ImageFont.truetype(str(FONT_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")), size)


F_CLOCK = font(22)
F_LABEL = font(14)
F_BIG = font(40)
F_SMALL = font(13, bold=False)


class Stats:
    def __init__(self):
        self._prev_cpu = self._read_cpu()
        self._gpu = None
        self._gpu_at = 0.0
        self._pump = cc.FanChannel("pump")
        self._fan = cc.FanChannel("fan")

    @staticmethod
    def _read_cpu():
        vals = list(map(int, Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]))
        idle = vals[3] + vals[4]
        return idle, sum(vals)

    def cpu_load(self):
        idle, total = self._read_cpu()
        pidle, ptotal = self._prev_cpu
        self._prev_cpu = (idle, total)
        dt = total - ptotal
        return 100.0 * (1 - (idle - pidle) / dt) if dt else 0.0

    def fans(self):
        return self._pump.rpm(), self._fan.rpm()

    @staticmethod
    def mem():
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, v = line.split(":", 1)
            info[k] = int(v.split()[0])
        total = info["MemTotal"] / 1048576
        return total - info["MemAvailable"] / 1048576, total

    def gpu(self):
        if time.monotonic() - self._gpu_at < 2:
            return self._gpu
        self._gpu_at = time.monotonic()
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2).stdout
            t, u, mu, mt = (float(x) for x in out.splitlines()[0].split(","))
            self._gpu = (t, u, mu / 1024, mt / 1024)
        except Exception:
            self._gpu = None
        return self._gpu


def temp_color(t, lcfg):
    if t is None:
        return DIM
    if t < lcfg["warm"]:
        return (80, 200, 120)
    if t < lcfg["hot"]:
        return (240, 180, 60)
    return (235, 80, 70)


def bar(d, x, y, w, h, frac, color):
    d.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=TRACK)
    fw = max(h, int(w * min(max(frac, 0), 1)))
    d.rounded_rectangle((x, y, x + fw, y + h), radius=h // 2, fill=color)


def panel(d, x0, label, temp, load, lcfg):
    c = temp_color(temp, lcfg)
    d.text((x0 + 12, 40), label, font=F_LABEL, fill=DIM)
    d.text((x0 + 12, 54), "--" if temp is None else f"{temp:.0f}°", font=F_BIG, fill=c)
    d.text((x0 + 12, 102), f"Load {load:.0f}%" if load is not None else "Load --", font=F_SMALL, fill=FG)
    bar(d, x0 + 12, 120, 136, 8, (load or 0) / 100, c)


def render(stats, lcfg):
    img = Image.new("RGB", (PANEL_W, PANEL_H), BG)
    d = ImageDraw.Draw(img)
    now = datetime.datetime.now()
    d.text((12, 6), now.strftime("%H:%M:%S" if lcfg["show_seconds"] else "%H:%M"), font=F_CLOCK, fill=FG)
    weekday = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[now.weekday()]
    d.text((PANEL_W - 12, 12), f"{weekday} {now:%m-%d}", font=F_SMALL, fill=DIM, anchor="ra")
    d.line((12, 34, PANEL_W - 12, 34), fill=TRACK, width=1)

    panel(d, 0, "CPU", cc.read_temp(lcfg["temp_source"]), stats.cpu_load(), lcfg)
    g = stats.gpu()
    panel(d, 160, "GPU", g[0] if g else None, g[1] if g else None, lcfg)
    d.line((160, 44, 160, 128), fill=TRACK, width=1)

    pump, fan = stats.fans()
    for x0, name, val in ((0, "PUMP", pump), (160, "FAN", fan)):
        d.text((x0 + 12, 140), name, font=F_LABEL, fill=DIM)
        d.text((x0 + 148, 141), "--" if val is None else f"{val} rpm", font=F_SMALL, fill=FG, anchor="ra")
    d.line((12, 164, PANEL_W - 12, 164), fill=TRACK, width=1)

    used, total = stats.mem()
    d.text((12, 170), "RAM", font=F_LABEL, fill=DIM)
    d.text((PANEL_W - 12, 172), f"{used:.1f} / {total:.0f} GB", font=F_SMALL, fill=FG, anchor="ra")
    bar(d, 12, 190, PANEL_W - 24, 8, used / total, (90, 150, 240))
    if g:
        d.text((12, 204), "VRAM", font=F_LABEL, fill=DIM)
        d.text((PANEL_W - 12, 206), f"{g[2]:.1f} / {g[3]:.0f} GB", font=F_SMALL, fill=FG, anchor="ra")
        bar(d, 12, 224, PANEL_W - 24, 8, g[2] / g[3], (170, 110, 230))
    return img


def to_signal(img):
    frame = Image.new("RGB", (OUT_W, OUT_H), (0, 0, 0))
    frame.paste(img.resize((PANEL_W * 2, PANEL_H), Image.NEAREST), (0, 0))
    return frame


def log(msg):
    print(msg, flush=True)


def open_display():
    import kms

    if kms.previous_attempt_failed():
        raise OSError(errno.EALREADY, "a modeset attempt already failed this boot "
                                      "(driver may be wedged); not touching the device until reboot")
    card = kms.find_card()
    if card is None:
        raise OSError(errno.ENODEV, "cooler LCD DRM device not found (is usbdisp_drm loaded?)")
    return kms.Display(card)


def probe():
    """Step-by-step self test of the direct KMS path."""
    try:
        disp = open_display()
    except OSError as e:
        log(f"FAIL open: {e}")
        return 1
    log(f"ok   card opened, connector {disp.connector_id} -> crtc {disp.crtc_id}, "
        f"fb {disp.fb_id} {disp.width}x{disp.height} pitch {disp.pitch}")
    try:
        disp.show(to_signal(render(Stats(), cc.load_config()["lcd"])).tobytes("raw", "BGRX"))
        log("ok   frame shown - check the cooler screen")
        time.sleep(5)
        return 0
    except OSError as e:
        if e.errno in (errno.EACCES, errno.EPERM):
            log("FAIL modeset refused: the desktop still owns the cooler LCD "
                "(run install.sh, then reboot once)")
        else:
            log(f"FAIL modeset: {e}")
        return 1
    finally:
        disp.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="test the KMS output once and exit")
    if ap.parse_args().probe:
        sys.exit(probe())

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    stats = Stats()
    cfg, mtime = cc.load_config(), cc.config_mtime()
    disp, last_err = None, None
    try:
        while True:
            if cc.config_mtime() != mtime:
                cfg, mtime = cc.load_config(), cc.config_mtime()
            if disp is None:
                try:
                    disp = open_display()
                except OSError as e:
                    if str(e) != last_err:
                        log(f"waiting for cooler LCD: {e}")
                        last_err = str(e)
                    time.sleep(10)
                    continue
            try:
                disp.show(to_signal(render(stats, cfg["lcd"])).tobytes("raw", "BGRX"))
                if last_err:
                    log("cooler LCD running")
                    last_err = None
            except OSError as e:
                disp.close()
                disp = None
                owned = e.errno in (errno.EACCES, errno.EPERM)
                msg = ("the desktop still owns the cooler LCD; run install.sh, then reboot once"
                       if owned else str(e))
                if msg != last_err:
                    log(msg)
                    last_err = msg
                time.sleep(30 if owned else 10)
                continue
            time.sleep(cfg["lcd"]["interval"])
    finally:
        if disp is not None:
            disp.close()


if __name__ == "__main__":
    main()
