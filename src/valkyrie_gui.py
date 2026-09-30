#!/usr/bin/env python3
"""Settings app for the Valkyrie B240 cooler: pump/fan control and LCD panel options."""
import copy
import gettext
import grp
import io
import os
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402
from PIL import Image  # noqa: E402

import cooler_common as cc  # noqa: E402
import valkyrie_lcd as lcd  # noqa: E402

# Follows LANGUAGE / LC_ALL / LC_MESSAGES / LANG; falls back to the English source strings.
_ = gettext.translation("valkyrie-b240", Path(__file__).resolve().parent / "locale", fallback=True).gettext

APP_ID = "io.github.yutong_feng.B240Cooler"
MODE_NAMES = [_("BIOS auto"), _("Fixed speed"), _("Temperature curve")]
SOURCE_NAMES = [_("CPU Tctl (k10temp)"), _("CPU die CCD1 (k10temp)"), _("Motherboard CPU sensor"),
                _("GPU (NVIDIA)"), _("Hotter of CPU and GPU")]
CHANNEL_TITLES = {"pump": _("Pump"), "fan": _("Radiator fans"),
                  **{s: _("System fan #{n}").format(n=s[3:]) for s in cc.SYS_FANS}}
FAN_SERVICE = "valkyrie-fan.service"
LCD_SERVICE = "valkyrie-lcd.service"
ACCENT = (0.21, 0.52, 0.89)
DANGER = (0.88, 0.27, 0.23)
WARN = (0.96, 0.62, 0.15)


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def service_active(unit):
    return systemctl("is-active", unit).stdout.strip() == "active"


class CurveEditor(Gtk.DrawingArea):
    PAD_L, PAD_R, PAD_T, PAD_B = 44, 18, 14, 30
    HIT = 14

    def __init__(self, floor, curve, on_changed):
        super().__init__()
        self.floor = floor
        self.curve = copy.deepcopy(curve)
        self.on_changed = on_changed
        self.temp = None
        self.drag_idx = None
        self.drag_origin = (0, 0)
        self.set_content_height(230)
        self.set_hexpand(True)
        self.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", self._drag_end)
        self.add_controller(drag)

    def set_curve(self, curve):
        self.curve = copy.deepcopy(curve)
        self.queue_draw()

    def _plot(self):
        w, h = self.get_width(), self.get_height()
        return self.PAD_L, self.PAD_T, w - self.PAD_L - self.PAD_R, h - self.PAD_T - self.PAD_B

    def _to_px(self, t, p):
        x0, y0, pw, ph = self._plot()
        return (x0 + (t - cc.CURVE_TMIN) / (cc.CURVE_TMAX - cc.CURVE_TMIN) * pw,
                y0 + (1 - p / 100) * ph)

    def _from_px(self, x, y):
        x0, y0, pw, ph = self._plot()
        return (cc.CURVE_TMIN + (x - x0) / pw * (cc.CURVE_TMAX - cc.CURVE_TMIN),
                (1 - (y - y0) / ph) * 100)

    def _draw(self, _area, cr, w, h):
        fg = self.get_color()
        x0, y0, pw, ph = self._plot()
        cr.select_font_face("Sans")
        cr.set_font_size(11)

        cr.set_source_rgba(*DANGER, 0.13)
        fy = self._to_px(0, self.floor)[1]
        cr.rectangle(x0, fy, pw, y0 + ph - fy)
        cr.fill()

        cr.set_line_width(1)
        for t in range(cc.CURVE_TMIN, cc.CURVE_TMAX + 1, 10):
            x = self._to_px(t, 0)[0]
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
            cr.move_to(x, y0)
            cr.line_to(x, y0 + ph)
            cr.stroke()
            if t % 20 == 0:
                cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
                label = f"{t}°C"
                ext = cr.text_extents(label)
                cr.move_to(x - ext.width / 2, y0 + ph + 18)
                cr.show_text(label)
        for p in range(0, 101, 25):
            y = self._to_px(0, p)[1]
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.10)
            cr.move_to(x0, y)
            cr.line_to(x0 + pw, y)
            cr.stroke()
            cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.6)
            label = f"{p}%"
            ext = cr.text_extents(label)
            cr.move_to(x0 - ext.width - 8, y + ext.height / 2)
            cr.show_text(label)

        pts = [self._to_px(t, max(p, self.floor)) for t, p in self.curve]
        line = [(x0, pts[0][1]), *pts, (x0 + pw, pts[-1][1])]
        cr.move_to(*line[0])
        for pt in line[1:]:
            cr.line_to(*pt)
        cr.line_to(x0 + pw, y0 + ph)
        cr.line_to(x0, y0 + ph)
        cr.close_path()
        cr.set_source_rgba(*ACCENT, 0.15)
        cr.fill()
        cr.move_to(*line[0])
        for pt in line[1:]:
            cr.line_to(*pt)
        cr.set_source_rgba(*ACCENT, 1)
        cr.set_line_width(2.5)
        cr.stroke()

        if self.temp is not None and cc.CURVE_TMIN <= self.temp <= cc.CURVE_TMAX:
            tx = self._to_px(self.temp, 0)[0]
            cr.set_source_rgba(*WARN, 0.9)
            cr.set_line_width(1.5)
            cr.set_dash([4, 4])
            cr.move_to(tx, y0)
            cr.line_to(tx, y0 + ph)
            cr.stroke()
            cr.set_dash([])
            target = max(cc.curve_value(self.curve, self.temp), self.floor)
            ty = self._to_px(0, target)[1]
            cr.new_path()
            cr.arc(tx, ty, 4, 0, 6.2832)
            cr.fill()
            label = _("Now {temp:.0f}°C → {pct:.0f}%").format(temp=self.temp, pct=target)
            ext = cr.text_extents(label)
            lx = tx + 6 if tx + 6 + ext.width < x0 + pw else tx - 6 - ext.width
            cr.move_to(lx, y0 + 12)
            cr.show_text(label)

        for i, (x, y) in enumerate(pts):
            cr.new_path()
            cr.arc(x, y, 7 if i == self.drag_idx else 6, 0, 6.2832)
            cr.set_source_rgba(*ACCENT, 1)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(2)
            cr.stroke()

    def _drag_begin(self, _g, x, y):
        best, best_d = None, self.HIT
        for i, (t, p) in enumerate(self.curve):
            px, py = self._to_px(t, max(p, self.floor))
            d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
            if d <= best_d:
                best, best_d = i, d
        self.drag_idx = best
        self.drag_origin = (x, y)
        self.queue_draw()

    def _drag_update(self, _g, dx, dy):
        i = self.drag_idx
        if i is None:
            return
        t, p = self._from_px(self.drag_origin[0] + dx, self.drag_origin[1] + dy)
        lo = self.curve[i - 1][0] + 1 if i > 0 else cc.CURVE_TMIN
        hi = self.curve[i + 1][0] - 1 if i < len(self.curve) - 1 else cc.CURVE_TMAX
        self.curve[i] = [round(min(max(t, lo), hi)), round(min(max(p, self.floor), 100))]
        self.queue_draw()

    def _drag_end(self, _g, _dx, _dy):
        if self.drag_idx is not None:
            self.drag_idx = None
            self.queue_draw()
            self.on_changed(copy.deepcopy(self.curve))


class CoolerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=_("B240 Cooler Control"))
        self.set_default_size(760, 860)
        self.cfg = cc.load_config()
        self._save_id = 0
        self._syncing = False
        self.chans = {n: cc.FanChannel(n) for n in cc.CHANNEL_LABEL}
        self.preview_stats = lcd.Stats()

        self.toasts = Adw.ToastOverlay()
        self.stack = Adw.ViewStack()
        self.toasts.set_child(self.stack)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))

        self.banner = Adw.Banner()
        self.banner.set_button_label(_("Authorize…"))
        self.banner.connect("button-clicked", self._authorize)

        view = Adw.ToolbarView()
        view.add_top_bar(header)
        view.add_top_bar(self.banner)
        view.set_content(self.toasts)
        self.set_content(view)

        self.stack.add_titled_with_icon(self._build_cooling_page(), "cooling", _("Cooling"),
                                        "power-profile-performance-symbolic")
        self.stack.add_titled_with_icon(self._build_lcd_page(), "lcd", _("Cooler LCD"), "video-display-symbolic")

        self._refresh()
        self._refresh_services()
        GLib.timeout_add_seconds(1, self._refresh)
        GLib.timeout_add_seconds(3, self._refresh_services)

    # ---------- cooling page ----------

    def _build_cooling_page(self):
        page = Adw.PreferencesPage()

        status = Adw.PreferencesGroup(title=_("Live status"))
        self.temp_row = self._status_row(status, _("CPU temperature"))
        self.gpu_row = self._status_row(status, _("GPU temperature"))
        self.chan_rows = {n: self._status_row(status, CHANNEL_TITLES[n]) for n in cc.COOLER_FANS}
        self.sys_summary_row = self._status_row(status, _("System fans"))
        self.fan_service_row = self._status_row(status, _("Fan control service"))
        page.add(status)

        self.editors, self.fixed_rows, self.curve_boxes, self.mode_rows = {}, {}, {}, {}
        for name in cc.COOLER_FANS:
            page.add(self._build_channel_group(name))
        page.add(self._build_sys_fans_group())

        safety = Adw.PreferencesGroup(
            title=_("Safety"),
            description=_("If the fan control service stops, crashes, or cannot read a temperature "
                          "for 3 seconds, the fans go back to BIOS control."))
        emerg = Adw.SpinRow.new_with_range(70, 100, 1)
        emerg.set_title(_("Emergency full-speed temperature"))
        emerg.set_subtitle(_("Run at full speed when the CPU or a fan's own temperature source reaches this (°C)"))
        emerg.set_value(self.cfg["fan"]["emergency_temp"])
        emerg.connect("notify::value", lambda r, _p: self._set(("fan", "emergency_temp"), r.get_value()))
        safety.add(emerg)
        safety.add(self._source_row(_("Temperature for pump and radiator fans"), "temp_source"))
        page.add(safety)
        return page

    def _source_row(self, title, key):
        row = Adw.ComboRow(title=title)
        row.set_model(Gtk.StringList.new(SOURCE_NAMES))
        row.set_selected(cc.TEMP_SOURCES.index(self.cfg["fan"][key]))
        row.connect("notify::selected", lambda r, _p: self._set(("fan", key), cc.TEMP_SOURCES[r.get_selected()]))
        return row

    def _build_sys_fans_group(self):
        group = Adw.PreferencesGroup(
            title=_("System fans"),
            description=_("Case fans on the motherboard's SYS_FAN1–6 headers; safety floor {floor}%. "
                          "Leave unused headers on BIOS auto.").format(floor=cc.FLOOR["sys1"]))
        group.add(self._source_row(_("Temperature for system fans"), "sys_temp_source"))
        self.sys_expanders = {}
        for name in cc.SYS_FANS:
            exp = Adw.ExpanderRow(title=CHANNEL_TITLES[name], subtitle="--")
            reset = self._build_channel_controls(name, exp.add_row)
            exp.add_suffix(reset)
            group.add(exp)
            self.sys_expanders[name] = exp
        return group

    def _status_row(self, group, title):
        row = Adw.ActionRow(title=title)
        label = Gtk.Label(label="--", css_classes=["numeric", "dim-label"])
        row.add_suffix(label)
        group.add(row)
        return label

    def _build_channel_group(self, name):
        group = Adw.PreferencesGroup(title=CHANNEL_TITLES[name],
                                     description=_("Safety floor {floor}%; lower settings run at the floor").format(floor=cc.FLOOR[name]))
        group.set_header_suffix(self._build_channel_controls(name, group.add))
        return group

    def _build_channel_controls(self, name, add):
        """Add mode / fixed-speed / curve controls for one fan via add(); returns its reset button."""
        ccfg = self.cfg["fan"]["channels"][name]
        floor = cc.FLOOR[name]

        reset = Gtk.Button(icon_name="edit-undo-symbolic", tooltip_text=_("Reset curve to default"),
                           css_classes=["flat"], valign=Gtk.Align.CENTER)
        reset.connect("clicked", lambda _b: self._reset_curve(name))

        mode = Adw.ComboRow(title=_("Control mode"))
        mode.set_model(Gtk.StringList.new(MODE_NAMES))
        mode.set_selected(cc.MODES.index(ccfg["mode"]))
        mode.connect("notify::selected", lambda r, _p: self._set_mode(name, cc.MODES[r.get_selected()]))
        add(mode)
        self.mode_rows[name] = mode

        fixed = Adw.ActionRow(title=_("Fixed speed"))
        adj = Gtk.Adjustment(value=ccfg["fixed"], lower=floor, upper=100, step_increment=1, page_increment=10)
        scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj, digits=0,
                          draw_value=True, hexpand=True, valign=Gtk.Align.CENTER)
        scale.set_value_pos(Gtk.PositionType.LEFT)
        scale.set_size_request(300, -1)
        scale.set_format_value_func(lambda _s, v: f"{v:.0f}%")
        adj.connect("value-changed",
                    lambda a: self._set(("fan", "channels", name, "fixed"), round(a.get_value())))
        fixed.add_suffix(scale)
        add(fixed)
        self.fixed_rows[name] = fixed

        editor = CurveEditor(floor, ccfg["curve"],
                             lambda curve: self._set(("fan", "channels", name, "curve"), curve))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["card"])
        box.set_margin_top(6)
        editor.set_margin_top(8)
        editor.set_margin_bottom(4)
        editor.set_margin_start(6)
        editor.set_margin_end(6)
        box.append(editor)
        hint = Gtk.Label(label=_("Drag the points: horizontal is temperature, vertical is fan speed; "
                                 "the red area is below the safety floor"),
                         css_classes=["caption", "dim-label"], margin_bottom=8)
        box.append(hint)
        add(box)
        self.editors[name] = editor
        self.curve_boxes[name] = box

        self._update_mode_visibility(name)
        return reset

    def _update_mode_visibility(self, name):
        m = self.cfg["fan"]["channels"][name]["mode"]
        self.fixed_rows[name].set_visible(m == "fixed")
        self.curve_boxes[name].set_visible(m == "curve")

    def _set_mode(self, name, mode):
        self.cfg["fan"]["channels"][name]["mode"] = mode
        self._update_mode_visibility(name)
        self._schedule_save()
        if mode != "bios":
            if not self.chans[name].writable():
                self._toast(_("Fan control is not authorized yet; click “Authorize…” at the top first"))
            if not service_active(FAN_SERVICE):
                r = systemctl("enable", "--now", FAN_SERVICE)
                if r.returncode:
                    self._toast(_("Failed to start the fan control service: {error}").format(error=r.stderr.strip()))

    def _reset_curve(self, name):
        curve = copy.deepcopy(cc.DEFAULTS["fan"]["channels"][name]["curve"])
        self.editors[name].set_curve(curve)
        self._set(("fan", "channels", name, "curve"), curve)
        self._toast(_("{fan} curve reset to default").format(fan=CHANNEL_TITLES[name]))

    # ---------- LCD page ----------

    def _build_lcd_page(self):
        page = Adw.PreferencesPage()
        lcfg = self.cfg["lcd"]

        preview = Adw.PreferencesGroup(title=_("Preview"), description=_("What the cooler's 2\" screen (320×240) is showing"))
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, halign=Gtk.Align.CENTER,
                                   css_classes=["card"])
        self.picture.set_size_request(480, 360)
        preview.add(self.picture)
        page.add(preview)

        panel = Adw.PreferencesGroup(title=_("Panel"))
        self.lcd_switch = Adw.SwitchRow(title=_("Show the monitor panel on the cooler"),
                                     subtitle=_("When off, the cooler shows its built-in logo"))
        self.lcd_switch.connect("notify::active", self._toggle_lcd)
        panel.add(self.lcd_switch)
        self.lcd_status = self._status_row(panel, _("Status"))
        interval = Adw.SpinRow.new_with_range(0.5, 5, 0.5)
        interval.set_title(_("Refresh interval (s)"))
        interval.set_digits(1)
        interval.set_value(lcfg["interval"])
        interval.connect("notify::value", lambda r, _p: self._set(("lcd", "interval"), r.get_value()))
        panel.add(interval)
        secs = Adw.SwitchRow(title=_("Show seconds on the clock"), active=lcfg["show_seconds"])
        secs.connect("notify::active", lambda r, _p: self._set(("lcd", "show_seconds"), r.get_active()))
        panel.add(secs)
        src = Adw.ComboRow(title=_("CPU temperature source"))
        src.set_model(Gtk.StringList.new(SOURCE_NAMES))
        src.set_selected(cc.TEMP_SOURCES.index(lcfg["temp_source"]))
        src.connect("notify::selected",
                    lambda r, _p: self._set(("lcd", "temp_source"), cc.TEMP_SOURCES[r.get_selected()]))
        panel.add(src)
        page.add(panel)

        colors = Adw.PreferencesGroup(title=_("Temperature colors"),
                                      description=_("Green below “Warm”, yellow in between, red from “Hot”"))
        self.warm_row = Adw.SpinRow.new_with_range(30, 95, 1)
        self.warm_row.set_title(_("Warm (°C)"))
        self.warm_row.set_value(lcfg["warm"])
        self.hot_row = Adw.SpinRow.new_with_range(31, 100, 1)
        self.hot_row.set_title(_("Hot (°C)"))
        self.hot_row.set_value(lcfg["hot"])
        self.warm_row.connect("notify::value", self._thresholds_changed)
        self.hot_row.connect("notify::value", self._thresholds_changed)
        colors.add(self.warm_row)
        colors.add(self.hot_row)
        page.add(colors)
        return page

    def _thresholds_changed(self, _row, _p):
        warm, hot = self.warm_row.get_value(), self.hot_row.get_value()
        if hot <= warm:
            self.hot_row.set_value(warm + 1)
            return
        self.cfg["lcd"]["warm"], self.cfg["lcd"]["hot"] = warm, hot
        self._schedule_save()

    def _toggle_lcd(self, row, _p):
        if self._syncing:
            return
        r = systemctl("enable" if row.get_active() else "disable", "--now", LCD_SERVICE)
        if r.returncode:
            self._toast(_("Failed to switch the panel service: {error}").format(error=r.stderr.strip()))

    # ---------- shared ----------

    def _set(self, path, value):
        d = self.cfg
        for k in path[:-1]:
            d = d[k]
        d[path[-1]] = value
        self._schedule_save()

    def _schedule_save(self):
        if self._save_id:
            GLib.source_remove(self._save_id)
        self._save_id = GLib.timeout_add(300, self._save)

    def _save(self):
        self._save_id = 0
        try:
            cc.save_config(self.cfg)
        except OSError as e:
            self._toast(_("Failed to save settings: {error}").format(error=e))
        return False

    def _toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    def _refresh(self):
        fcfg = self.cfg["fan"]
        temps = {src: cc.read_temp(src) for src in {fcfg["temp_source"], fcfg["sys_temp_source"], "tctl", "gpu"}}
        fmt = lambda v: "--" if v is None else f"{v:.1f} °C"  # noqa: E731
        self.temp_row.set_label(fmt(temps["tctl"]))
        self.gpu_row.set_label(fmt(temps["gpu"]))
        driver = cc.find_hwmon("nct6687") is not None
        writable = True
        connected = []
        for name, ch in self.chans.items():
            rpm, pct, auto = ch.rpm(), ch.pwm_percent(), ch.is_auto()
            writable &= ch.writable()
            if rpm is None:
                text = "--"
            elif rpm == 0 and name in cc.SYS_FANS and auto:
                text = _("Not connected")
            else:
                owner = _("BIOS control") if auto else _("Controlled by this app")
                text = f"{rpm} rpm · {pct:.0f}% · {owner}"
            if name in cc.SYS_FANS:
                self.sys_expanders[name].set_subtitle(text)
                if rpm:
                    connected.append(f"#{name[3:]} {rpm} rpm")
            else:
                self.chan_rows[name].set_label(text)
            self.editors[name].temp = temps[cc.channel_temp_source(fcfg, name)]
            self.editors[name].queue_draw()
        self.sys_summary_row.set_label(_(", ").join(connected) if connected else _("No connected fans detected"))

        if not driver:
            self.banner.set_title(_("Fan driver nct6687 not found; fan speeds and control are unavailable"))
            self.banner.set_button_label(None)
            self.banner.set_revealed(True)
        elif not writable:
            self.banner.set_title(_("Controlling the pump and fans needs a one-time authorization (installs a udev rule)"))
            self.banner.set_button_label(_("Authorize…"))
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

        if self.stack.get_visible_child_name() == "lcd":
            img = lcd.render(self.preview_stats, self.cfg["lcd"]).resize((480, 360), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            self.picture.set_paintable(Gdk.Texture.new_from_bytes(GLib.Bytes.new(buf.getvalue())))
        return True

    def _refresh_services(self):
        fan_on = service_active(FAN_SERVICE)
        managed = any(c["mode"] != "bios" for c in self.cfg["fan"]["channels"].values())
        self.fan_service_row.set_label(
            (_("Running") if managed else _("Standby (all fans on BIOS control)")) if fan_on else _("Not running"))
        lcd_on = service_active(LCD_SERVICE)
        self._syncing = True
        self.lcd_switch.set_active(lcd_on)
        self._syncing = False
        self.lcd_status.set_label(self._lcd_state() if lcd_on else _("Off"))
        return True

    @staticmethod
    def _lcd_state():
        last = subprocess.run(["journalctl", "--user", "-u", LCD_SERVICE, "-n", "20", "-o", "cat", "--no-pager"],
                              capture_output=True, text=True).stdout.strip().splitlines()
        starts = [i for i, ln in enumerate(last) if ln.startswith("Started")]
        since = last[starts[-1] + 1:] if starts else last
        msgs = [ln for ln in since if not ln.startswith(("Stopped", "Stopping", "valkyrie-lcd"))]
        if not msgs or msgs[-1] == "cooler LCD running":
            return _("Running")
        if "desktop still owns" in msgs[-1]:
            return _("Waiting for reboot: the desktop still owns the cooler LCD")
        if "already failed this boot" in msgs[-1]:
            return _("Driver error; paused until the next reboot")
        if "not found" in msgs[-1]:
            return _("Cooler LCD driver not found")
        return msgs[-1][:60]

    def _authorize(self, _banner):
        group = grp.getgrgid(os.getgid()).gr_name
        script = (f'printf "%s" "$1" > {cc.UDEV_RULE_PATH} && udevadm control --reload && '
                  "udevadm trigger --action=add --subsystem-match=hwmon --attr-match=name=nct6687 && "
                  "udevadm settle")
        proc = Gio.Subprocess.new(["pkexec", "/bin/sh", "-c", script, "sh", cc.udev_rule(group)],
                                  Gio.SubprocessFlags.STDERR_PIPE)
        proc.wait_async(None, self._authorize_done)

    def _authorize_done(self, proc, res):
        proc.wait_finish(res)
        if proc.get_successful() and all(ch.writable() for ch in self.chans.values()):
            self._toast(_("Authorized; the pump and fans can now be controlled"))
            systemctl("restart", FAN_SERVICE)
        else:
            self._toast(_("Authorization was not completed"))
        self._refresh()


class CoolerApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self):
        win = self.props.active_window or CoolerWindow(self)
        win.present()


if __name__ == "__main__":
    # WM_CLASS comes from prgname; matching the .desktop name lets GNOME show our icon
    GLib.set_prgname(APP_ID)
    Gtk.Window.set_default_icon_name(APP_ID)
    sys.exit(CoolerApp().run(sys.argv))
