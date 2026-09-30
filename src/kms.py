"""Minimal libdrm/KMS output: one connector, one CRTC, one dumb framebuffer."""
import ctypes
import errno
import fcntl
import mmap
import os
import re
import struct
import time
from ctypes import POINTER, byref, c_char, c_int, c_uint16, c_uint32, c_uint64
from pathlib import Path

_drm = ctypes.CDLL("libdrm.so.2", use_errno=True)

DRM_MODE_CONNECTED = 1
DRM_MODE_FLAG_NHSYNC = 1 << 1
DRM_MODE_FLAG_NVSYNC = 1 << 3
DRM_MODE_TYPE_USERDEF = 1 << 5

DRM_IOCTL_MODE_CREATE_DUMB = 0xC02064B2
DRM_IOCTL_MODE_MAP_DUMB = 0xC01064B3
DRM_IOCTL_MODE_DESTROY_DUMB = 0xC00464B4


class ModeInfo(ctypes.Structure):
    _fields_ = [("clock", c_uint32),
                ("hdisplay", c_uint16), ("hsync_start", c_uint16), ("hsync_end", c_uint16),
                ("htotal", c_uint16), ("hskew", c_uint16),
                ("vdisplay", c_uint16), ("vsync_start", c_uint16), ("vsync_end", c_uint16),
                ("vtotal", c_uint16), ("vscan", c_uint16),
                ("vrefresh", c_uint32), ("flags", c_uint32), ("type", c_uint32), ("name", c_char * 32)]


class Resources(ctypes.Structure):
    _fields_ = [("count_fbs", c_int), ("fbs", POINTER(c_uint32)),
                ("count_crtcs", c_int), ("crtcs", POINTER(c_uint32)),
                ("count_connectors", c_int), ("connectors", POINTER(c_uint32)),
                ("count_encoders", c_int), ("encoders", POINTER(c_uint32)),
                ("min_width", c_uint32), ("max_width", c_uint32),
                ("min_height", c_uint32), ("max_height", c_uint32)]


class Connector(ctypes.Structure):
    _fields_ = [("connector_id", c_uint32), ("encoder_id", c_uint32),
                ("connector_type", c_uint32), ("connector_type_id", c_uint32),
                ("connection", c_int), ("mmWidth", c_uint32), ("mmHeight", c_uint32), ("subpixel", c_int),
                ("count_modes", c_int), ("modes", POINTER(ModeInfo)),
                ("count_props", c_int), ("props", POINTER(c_uint32)), ("prop_values", POINTER(c_uint64)),
                ("count_encoders", c_int), ("encoders", POINTER(c_uint32))]


class Encoder(ctypes.Structure):
    _fields_ = [("encoder_id", c_uint32), ("encoder_type", c_uint32), ("crtc_id", c_uint32),
                ("possible_crtcs", c_uint32), ("possible_clones", c_uint32)]


class Clip(ctypes.Structure):
    _fields_ = [("x1", c_uint16), ("y1", c_uint16), ("x2", c_uint16), ("y2", c_uint16)]


_drm.drmModeGetResources.restype = POINTER(Resources)
_drm.drmModeGetResources.argtypes = [c_int]
_drm.drmModeFreeResources.argtypes = [POINTER(Resources)]
_drm.drmModeGetConnector.restype = POINTER(Connector)
_drm.drmModeGetConnector.argtypes = [c_int, c_uint32]
_drm.drmModeFreeConnector.argtypes = [POINTER(Connector)]
_drm.drmModeGetEncoder.restype = POINTER(Encoder)
_drm.drmModeGetEncoder.argtypes = [c_int, c_uint32]
_drm.drmModeFreeEncoder.argtypes = [POINTER(Encoder)]
_drm.drmModeAddFB.argtypes = [c_int, c_uint32, c_uint32, ctypes.c_uint8, ctypes.c_uint8,
                              c_uint32, c_uint32, POINTER(c_uint32)]
_drm.drmModeRmFB.argtypes = [c_int, c_uint32]
_drm.drmModeSetCrtc.argtypes = [c_int, c_uint32, c_uint32, c_uint32, c_uint32,
                                POINTER(c_uint32), c_int, POINTER(ModeInfo)]
_drm.drmModeDirtyFB.argtypes = [c_int, c_uint32, POINTER(Clip), c_uint32]


def _check(ret, what):
    if ret < 0:
        err = -ret if ret != -1 else ctypes.get_errno()
        raise OSError(err, f"{what}: {os.strerror(err)}")
    return ret


def mode_640x480_60():
    # VESA 640x480@60: the only mode the cooler's panel displays (EDID-listed modes show just the logo)
    return ModeInfo(clock=25175, hdisplay=640, hsync_start=656, hsync_end=752, htotal=800, hskew=0,
                    vdisplay=480, vsync_start=490, vsync_end=492, vtotal=525, vscan=0, vrefresh=60,
                    flags=DRM_MODE_FLAG_NHSYNC | DRM_MODE_FLAG_NVSYNC, type=DRM_MODE_TYPE_USERDEF,
                    name=b"640x480")


# The msdisp driver copies frames using the size of whatever mode the hardware was last
# enabled with (fbdev leaves it at 800x600), not the framebuffer's size, so a framebuffer
# smaller than that mode is read out of bounds and oopses the kernel. Allocate for the
# largest mode the driver supports and draw into the top-left corner.
ALLOC_W, ALLOC_H = 1920, 1080

ATTEMPT_MARKER = Path.home() / ".cache" / "valkyrie-cooler" / "modeset-attempt"


def _boot_id():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def previous_attempt_failed():
    """True if a modeset started this boot never completed (the driver may be wedged)."""
    try:
        return ATTEMPT_MARKER.read_text().strip() == _boot_id()
    except FileNotFoundError:
        return False


def find_card(driver="msdisp_plat"):
    for c in sorted(Path("/sys/class/drm").iterdir()):
        if re.fullmatch(r"card\d+", c.name) and (c / "device" / "driver").resolve().name == driver:
            return Path("/dev/dri") / c.name
    return None


class Display:
    def __init__(self, card, mode=None):
        self.mode = mode or mode_640x480_60()
        self.width, self.height = self.mode.hdisplay, self.mode.vdisplay
        self.fd = os.open(card, os.O_RDWR | os.O_CLOEXEC)
        self.fb_id = self.handle = 0
        self.map = None
        self.crtc_set = False
        try:
            self.connector_id, self.crtc_id = self._pick_pipe()
            self._create_fb()
        except Exception:
            self.close()
            raise

    def _pick_pipe(self):
        res = _drm.drmModeGetResources(self.fd)
        if not res:
            _check(-1, "drmModeGetResources")
        try:
            r = res.contents
            crtcs = [r.crtcs[i] for i in range(r.count_crtcs)]
            for i in range(r.count_connectors):
                conn = _drm.drmModeGetConnector(self.fd, r.connectors[i])
                if not conn:
                    continue
                try:
                    c = conn.contents
                    if c.connection != DRM_MODE_CONNECTED:
                        continue
                    enc_ids = [c.encoder_id] if c.encoder_id else [c.encoders[j] for j in range(c.count_encoders)]
                    for eid in enc_ids:
                        enc = _drm.drmModeGetEncoder(self.fd, eid)
                        if not enc:
                            continue
                        try:
                            e = enc.contents
                            if e.crtc_id:
                                return c.connector_id, e.crtc_id
                            for bit, crtc in enumerate(crtcs):
                                if e.possible_crtcs & (1 << bit):
                                    return c.connector_id, crtc
                        finally:
                            _drm.drmModeFreeEncoder(enc)
                finally:
                    _drm.drmModeFreeConnector(conn)
        finally:
            _drm.drmModeFreeResources(res)
        raise OSError("no connected output on the cooler LCD")

    def _create_fb(self):
        aw, ah = max(self.width, ALLOC_W), max(self.height, ALLOC_H)
        buf = bytearray(struct.pack("<IIIIIIQ", ah, aw, 32, 0, 0, 0, 0))
        fcntl.ioctl(self.fd, DRM_IOCTL_MODE_CREATE_DUMB, buf)
        *_, self.handle, self.pitch, self.size = struct.unpack("<IIIIIIQ", buf)
        buf = bytearray(struct.pack("<IIQ", self.handle, 0, 0))
        fcntl.ioctl(self.fd, DRM_IOCTL_MODE_MAP_DUMB, buf)
        offset = struct.unpack("<IIQ", buf)[2]
        self.map = mmap.mmap(self.fd, self.size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                             offset=offset)
        fb = c_uint32()
        _check(_drm.drmModeAddFB(self.fd, aw, ah, 24, 32, self.pitch, self.handle, byref(fb)),
               "drmModeAddFB")
        self.fb_id = fb.value

    def show(self, bgrx):
        """Push one width x height frame of BGRX bytes to the screen."""
        row = self.width * 4
        if self.pitch == row:
            self.map[:len(bgrx)] = bgrx
        else:
            for y in range(self.height):
                self.map[y * self.pitch:y * self.pitch + row] = bgrx[y * row:(y + 1) * row]
        if not self.crtc_set:
            self._set_crtc()
            return
        clip = Clip(0, 0, self.width, self.height)
        if _drm.drmModeDirtyFB(self.fd, self.fb_id, byref(clip), 1) < 0:
            self._set_crtc()

    def _set_crtc(self):
        first = not self.crtc_set
        if first:
            if previous_attempt_failed():
                raise OSError(errno.EALREADY, "a modeset attempt already failed this boot; "
                                              "not retrying until reboot")
            ATTEMPT_MARKER.parent.mkdir(parents=True, exist_ok=True)
            ATTEMPT_MARKER.write_text(_boot_id())
            # Disable first: while the hardware is off the driver drops frames instead of
            # copying them with the previous mode's geometry.
            ret = _drm.drmModeSetCrtc(self.fd, self.crtc_id, 0, 0, 0, None, 0, None)
            if ret < 0:
                # not master (desktop owns the device) is not a driver failure
                ATTEMPT_MARKER.unlink(missing_ok=True)
                _check(ret, "disable crtc")
            time.sleep(0.3)
        conn = c_uint32(self.connector_id)
        _check(_drm.drmModeSetCrtc(self.fd, self.crtc_id, self.fb_id, 0, 0, byref(conn), 1, byref(self.mode)),
               "drmModeSetCrtc")
        self.crtc_set = True
        if first:
            ATTEMPT_MARKER.unlink(missing_ok=True)

    def close(self):
        if self.fd < 0:
            return
        if self.crtc_set:
            # blank the output so the cooler falls back to its own logo
            _drm.drmModeSetCrtc(self.fd, self.crtc_id, 0, 0, 0, None, 0, None)
        if self.fb_id:
            _drm.drmModeRmFB(self.fd, self.fb_id)
        if self.map is not None:
            self.map.close()
        if self.handle:
            try:
                fcntl.ioctl(self.fd, DRM_IOCTL_MODE_DESTROY_DUMB, struct.pack("<I", self.handle))
            except OSError:
                pass
        os.close(self.fd)
        self.fd = -1
