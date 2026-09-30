"""Shared config and sensor access for the Valkyrie B240 cooler tools."""
import copy
import json
import os
import subprocess
import time
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "valkyrie-cooler" / "config.json"

SYS_FANS = tuple(f"sys{i}" for i in range(1, 7))
COOLER_FANS = ("pump", "fan")
# Enforced by the fan daemon regardless of what the config says.
FLOOR = {"pump": 50, "fan": 20, **{s: 20 for s in SYS_FANS}}
CHANNEL_LABEL = {"pump": "Pump Fan", "fan": "CPU Fan",
                 **{s: f"System Fan #{s[3:]}" for s in SYS_FANS}}
MODES = ("bios", "fixed", "curve")
TEMP_SOURCES = ("tctl", "tccd", "board", "gpu", "max")
CURVE_POINTS = 5
CURVE_TMIN, CURVE_TMAX = 20, 100

DEFAULTS = {
    "lcd": {
        "interval": 1.0,
        "temp_source": "tctl",
        "warm": 60,
        "hot": 80,
        "show_seconds": True,
    },
    "fan": {
        "temp_source": "tctl",
        # case fans mostly exhaust GPU heat, so follow whichever of CPU/GPU is hotter
        "sys_temp_source": "max",
        "emergency_temp": 95,
        "channels": {
            "pump": {"mode": "bios", "fixed": 100,
                     "curve": [[30, 70], [50, 80], [65, 90], [75, 100], [90, 100]]},
            "fan": {"mode": "bios", "fixed": 70,
                    "curve": [[30, 25], [50, 40], [65, 60], [75, 85], [85, 100]]},
            **{s: {"mode": "bios", "fixed": 50,
                   "curve": [[30, 30], [45, 40], [60, 60], [70, 80], [80, 100]]} for s in SYS_FANS},
        },
    },
}


def channel_temp_source(fcfg, name):
    return fcfg["sys_temp_source"] if name in SYS_FANS else fcfg["temp_source"]


def _clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _normalize_curve(curve, default):
    if not isinstance(curve, list) or len(curve) != CURVE_POINTS:
        return copy.deepcopy(default)
    pts = []
    for p, d in zip(curve, default):
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            return copy.deepcopy(default)
        pts.append([_clamp(p[0], CURVE_TMIN, CURVE_TMAX, d[0]), _clamp(p[1], 0, 100, d[1])])
    pts.sort(key=lambda p: p[0])
    return pts


def normalize(cfg):
    """Return a fully-populated, range-checked copy of cfg."""
    out = copy.deepcopy(DEFAULTS)
    if not isinstance(cfg, dict):
        return out
    lcd, dl = cfg.get("lcd", {}), DEFAULTS["lcd"]
    if isinstance(lcd, dict):
        out["lcd"]["interval"] = _clamp(lcd.get("interval"), 0.5, 5, dl["interval"])
        if lcd.get("temp_source") in TEMP_SOURCES:
            out["lcd"]["temp_source"] = lcd["temp_source"]
        out["lcd"]["warm"] = _clamp(lcd.get("warm"), 30, 95, dl["warm"])
        out["lcd"]["hot"] = _clamp(lcd.get("hot"), out["lcd"]["warm"] + 1, 100, dl["hot"])
        out["lcd"]["show_seconds"] = bool(lcd.get("show_seconds", dl["show_seconds"]))
    fan, df = cfg.get("fan", {}), DEFAULTS["fan"]
    if isinstance(fan, dict):
        if fan.get("temp_source") in TEMP_SOURCES:
            out["fan"]["temp_source"] = fan["temp_source"]
        if fan.get("sys_temp_source") in TEMP_SOURCES:
            out["fan"]["sys_temp_source"] = fan["sys_temp_source"]
        out["fan"]["emergency_temp"] = _clamp(fan.get("emergency_temp"), 70, 100, df["emergency_temp"])
        chans = fan.get("channels", {})
        for name, dch in df["channels"].items():
            ch = chans.get(name, {}) if isinstance(chans, dict) else {}
            if not isinstance(ch, dict):
                continue
            o = out["fan"]["channels"][name]
            if ch.get("mode") in MODES:
                o["mode"] = ch["mode"]
            o["fixed"] = _clamp(ch.get("fixed"), FLOOR[name], 100, dch["fixed"])
            o["curve"] = _normalize_curve(ch.get("curve"), dch["curve"])
    return out


def load_config():
    try:
        return normalize(json.loads(CONFIG_PATH.read_text()))
    except (OSError, ValueError):
        return normalize({})


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(normalize(cfg), indent=2, ensure_ascii=False))
    os.replace(tmp, CONFIG_PATH)


def config_mtime():
    try:
        return CONFIG_PATH.stat().st_mtime
    except OSError:
        return 0.0


def find_hwmon(name):
    for h in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (h / "name").read_text().strip() == name:
                return h
        except OSError:
            pass
    return None


def _read_int(path):
    return int(Path(path).read_text().strip())


def _find_by_label(hwmon, prefix, label):
    for f in hwmon.glob(f"{prefix}*_label"):
        if f.read_text().strip() == label:
            return f.name[: -len("_label")]
    return None


_gpu_cache = {"t": None, "at": -1e9}


def read_gpu_temp():
    """NVIDIA GPU temperature in degC (cached for 2 s, nvidia-smi is slow to spawn), or None."""
    if time.monotonic() - _gpu_cache["at"] >= 2:
        _gpu_cache["at"] = time.monotonic()
        try:
            out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=2).stdout
            _gpu_cache["t"] = float(out.split()[0])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            _gpu_cache["t"] = None
    return _gpu_cache["t"]


def read_temp(source):
    """Temperature in degC from the chosen source, or None."""
    if source == "gpu":
        return read_gpu_temp()
    if source == "max":
        temps = [t for t in (read_temp("tctl"), read_gpu_temp()) if t is not None]
        return max(temps) if temps else None
    try:
        if source == "board":
            h = find_hwmon("nct6687")
            key = h and _find_by_label(h, "temp", "CPU")
        else:
            h = find_hwmon("k10temp")
            key = h and _find_by_label(h, "temp", "Tccd1" if source == "tccd" else "Tctl")
        return _read_int(h / f"{key}_input") / 1000 if key else None
    except (OSError, ValueError):
        return None


class FanChannel:
    """One Super I/O fan header (rpm + pwm), located by its label."""

    def __init__(self, name):
        self.name = name
        self.hwmon = None
        self.idx = None

    def _locate(self):
        if self.hwmon and self.idx and (self.hwmon / "name").exists():
            return True
        self.hwmon = find_hwmon("nct6687")
        if not self.hwmon:
            return False
        key = _find_by_label(self.hwmon, "fan", CHANNEL_LABEL[self.name])
        self.idx = key[3:] if key else None
        return self.idx is not None

    def rpm(self):
        try:
            return _read_int(self.hwmon / f"fan{self.idx}_input") if self._locate() else None
        except (OSError, ValueError):
            self.hwmon = None
            return None

    def pwm_percent(self):
        try:
            return _read_int(self.hwmon / f"pwm{self.idx}") * 100 / 255 if self._locate() else None
        except (OSError, ValueError):
            self.hwmon = None
            return None

    def is_auto(self):
        try:
            return _read_int(self.hwmon / f"pwm{self.idx}_enable") == 2 if self._locate() else None
        except (OSError, ValueError):
            self.hwmon = None
            return None

    def writable(self):
        return bool(self._locate()) and os.access(self.hwmon / f"pwm{self.idx}", os.W_OK) \
            and os.access(self.hwmon / f"pwm{self.idx}_enable", os.W_OK)

    def set_percent(self, pct):
        if not self._locate():
            raise OSError("nct6687 not found")
        (self.hwmon / f"pwm{self.idx}").write_text(str(round(max(0, min(100, pct)) * 255 / 100)))

    def set_auto(self):
        if not self._locate():
            raise OSError("nct6687 not found")
        (self.hwmon / f"pwm{self.idx}_enable").write_text("2")


def curve_value(curve, t):
    if t <= curve[0][0]:
        return curve[0][1]
    for (t0, p0), (t1, p1) in zip(curve, curve[1:]):
        if t <= t1:
            return p0 if t1 == t0 else p0 + (p1 - p0) * (t - t0) / (t1 - t0)
    return curve[-1][1]


UDEV_RULE_PATH = "/etc/udev/rules.d/99-valkyrie-fan.rules"


def udev_rule(group):
    files = "/sys%p/pwm[1-8] /sys%p/pwm[1-8]_enable"
    return (f'ACTION=="add", SUBSYSTEM=="hwmon", ATTR{{name}}=="nct6687", '
            f"RUN+=\"/bin/sh -c 'chgrp {group} {files} && chmod g+w {files}'\"\n")
