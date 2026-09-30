#!/usr/bin/env python3
"""Generate the app icon SVG: black line-art fan with a water-drop hub on a white squircle."""
import math
import sys
from pathlib import Path

APP_ID = "io.github.yutong_feng.B240Cooler"
OUT = Path(__file__).resolve().parent.parent / "data" / "icons" / f"{APP_ID}.svg"

C = 512            # canvas center (1024 x 1024 canvas)
TILE = 412         # squircle half-size, macOS icon grid (824 px tile)
STROKE = 30
INK = "#1c1c1e"
BLADES = 5
HUB = 104          # hub radius; blades grow out of it
FAN = 286          # blade tip radius


def polar(r, deg):
    a = math.radians(deg)
    return C + r * math.cos(a), C + r * math.sin(a)


def fmt(p):
    return f"{p[0]:.1f} {p[1]:.1f}"


def squircle(half, n=4.6, steps=720):
    """Superellipse |x|^n + |y|^n = half^n, the continuous-corner shape of Apple app icons."""
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = half * math.copysign(abs(c) ** (2 / n), c)
        y = half * math.copysign(abs(s) ** (2 / n), s)
        pts.append((C + x, C + y))
    return "M" + " L".join(fmt(p) for p in pts) + " Z"


def blade(phi):
    """Open outline of one swept blade growing out of the hub; phi is its root angle in degrees."""
    p0, p2 = polar(HUB, phi - 14), polar(HUB, phi + 22)
    t1, t2 = polar(FAN, phi + 22), polar(FAN - 25, phi + 52)
    c1, c2 = polar(HUB + 70, phi - 22), polar(FAN - 10, phi + 2)
    c3, c4 = polar(FAN - 60, phi + 50), polar(HUB + 45, phi + 34)
    return (f"M{fmt(p0)} C{fmt(c1)} {fmt(c2)} {fmt(t1)} A{FAN} {FAN} 0 0 1 {fmt(t2)} "
            f"C{fmt(c3)} {fmt(c4)} {fmt(p2)}")


def drop(cy=C + 16, r=34, height=96):
    """Water-drop outline centered in the hub: a circle of radius r drawn up to a point."""
    top_y = cy + r - height
    return (f"M{C} {top_y:.1f} C{C - 8} {top_y + 26:.1f} {C - r} {cy - r * 0.6:.1f} {C - r} {cy} "
            f"A{r} {r} 0 0 0 {C + r} {cy} "
            f"C{C + r} {cy - r * 0.6:.1f} {C + 8} {top_y + 26:.1f} {C} {top_y:.1f} Z")


def svg():
    blades = "\n    ".join(f'<path d="{blade(-90 + i * 360 / BLADES)}"/>' for i in range(BLADES))
    tile = squircle(TILE)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff"/>
      <stop offset="1" stop-color="#ececef"/>
    </linearGradient>
    <filter id="shadow" x="-10%" y="-10%" width="120%" height="125%">
      <feGaussianBlur in="SourceAlpha" stdDeviation="14"/>
      <feOffset dy="12"/>
      <feComponentTransfer><feFuncA type="linear" slope="0.28"/></feComponentTransfer>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <path d="{tile}" fill="url(#bg)" filter="url(#shadow)"/>
  <path d="{tile}" fill="none" stroke="#000000" stroke-opacity="0.08" stroke-width="2"/>
  <g fill="none" stroke="{INK}" stroke-width="{STROKE}" stroke-linecap="round" stroke-linejoin="round">
    {blades}
    <circle cx="{C}" cy="{C}" r="{HUB}"/>
    <path d="{drop()}"/>
  </g>
</svg>
"""


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    out.write_text(svg())
    print("wrote", out)
