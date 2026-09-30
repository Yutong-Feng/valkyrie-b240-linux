#!/bin/bash
# Installer for the Valkyrie B240 Linux tools (the app is installed per user).
#   ./install.sh              install or update; offers to install missing drivers and packages
#   ./install.sh --drivers    (re)build and install both kernel drivers only
#   ./install.sh --user-only  update the app only (no sudo; drivers and udev rules already present)
#   ./install.sh --uninstall  remove the app and udev rules (drivers are kept)
set -euo pipefail

APP_ID=io.github.yutong_feng.B240Cooler
REPO=$(cd "$(dirname "$0")" && pwd)
LIBDIR="$HOME/.local/share/valkyrie-b240"
UNITDIR="$HOME/.config/systemd/user"
APPDIR="$HOME/.local/share/applications"
ICONDIR="$HOME/.local/share/icons/hicolor/scalable/apps"
LCD_RULE=/etc/udev/rules.d/72-valkyrie-lcd.rules
FAN_RULE=/etc/udev/rules.d/99-valkyrie-fan.rules
SERVICES=(valkyrie-lcd.service valkyrie-fan.service)
GROUP=$(id -gn)

APP_PKGS=(python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-pil libdrm2 gettext)
DRIVER_PKGS=(dkms git make gcc rsync "linux-headers-$(uname -r)")

# Driver revisions this project was tested with; bump after verifying a newer upstream commit.
LCD_DRIVER_REPO=https://github.com/AFSFerreira/ms91xx-linux-drm.git
LCD_DRIVER_REV=40ce8417b152d4db46c81bdfc5bded5a5f1c3813
FAN_DRIVER_REPO=https://github.com/Fred78290/nct6687d.git
FAN_DRIVER_REV=5f12dd1b0b3c8f79f31d309749862d986ff9efa7

WORK=""
trap '[ -n "$WORK" ] && rm -rf "$WORK"' EXIT

info() { printf '\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*"; }
confirm() {
    local ans
    read -r -p "$1 [Y/n] " ans || true
    [[ -z "$ans" || "$ans" =~ ^[Yy] ]]
}

app_deps_ok() {
    command -v msgfmt >/dev/null && /usr/bin/python3 - <<'EOF' 2>/dev/null
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_foreign("cairo")
from gi.repository import Adw, Gtk
from PIL import Image
import ctypes
ctypes.CDLL("libdrm.so.2")
EOF
}

check_deps() {
    app_deps_ok && return
    echo "Missing packages: ${APP_PKGS[*]}"
    if [ "${1:-}" = interactive ] && confirm "Install them now with apt?"; then
        sudo apt-get install -y "${APP_PKGS[@]}"
        app_deps_ok && return
    fi
    echo "Install them with: sudo apt install ${APP_PKGS[*]}"
    exit 1
}

have_lcd_driver() { modinfo usbdisp_usb >/dev/null 2>&1; }
have_fan_driver() { modinfo nct6687 >/dev/null 2>&1; }
# nct6687d targets MSI boards; elsewhere the in-kernel nct6683 may be the right driver.
is_msi_board() { grep -qi "micro-star" /sys/class/dmi/id/board_vendor 2>/dev/null; }

lcd_present() {
    local d
    for d in /sys/bus/usb/devices/*; do
        case "$(cat "$d/idVendor" 2>/dev/null):$(cat "$d/idProduct" 2>/dev/null)" in
            345f:9132 | 534d:6021) return 0 ;;
        esac
    done
    return 1
}

fetch() {  # fetch <repo> <rev> <dir>
    [ -n "$WORK" ] || WORK=$(mktemp -d)
    git clone -q "$1" "$WORK/$3"
    git -C "$WORK/$3" checkout -q "$2"
}

install_lcd_driver() {
    info "Building the cooler LCD driver (ms91xx-linux-drm)"
    lcd_present || warn "cooler LCD USB device (345f:9132) not found; installing the driver anyway"
    fetch "$LCD_DRIVER_REPO" "$LCD_DRIVER_REV" msdisp
    local ver old
    ver=$(sed -n 's/^PACKAGE_VERSION="\(.*\)"$/\1/p' "$WORK/msdisp/dkms.conf")
    for old in $(dkms status msdisp 2>/dev/null | sed -n 's|^msdisp/\([^,:]*\).*|\1|p' | sort -u); do
        sudo dkms remove "msdisp/$old" --all
    done
    sudo rsync -a --delete --exclude .git "$WORK/msdisp/" "/usr/src/msdisp-$ver/"
    sudo dkms add "msdisp/$ver"
    sudo dkms install "msdisp/$ver"
}

install_fan_driver() {
    info "Building the fan driver (nct6687d)"
    fetch "$FAN_DRIVER_REPO" "$FAN_DRIVER_REV" nct6687d
    make -C "$WORK/nct6687d" dkms/install
    echo nct6687 | sudo tee /etc/modules-load.d/nct6687.conf >/dev/null
    echo "blacklist nct6683" | sudo tee /etc/modprobe.d/nct6683-blacklist.conf >/dev/null
    sudo modprobe -r nct6683 2>/dev/null || true
    sudo modprobe nct6687 || warn "nct6687 could not be loaded now; it will load after a reboot"
}

install_drivers() {  # install_drivers lcd|fan ...
    info "Installing build dependencies"
    sudo apt-get install -y "${DRIVER_PKGS[@]}"
    if mokutil --sb-state 2>/dev/null | grep -qi enabled; then
        warn "Secure Boot is on: DKMS signs the modules with a MOK key; enroll it in the blue MOK screen on the next boot"
    fi
    local d
    for d in "$@"; do "install_${d}_driver"; done
}

ensure_drivers() {
    local missing=()
    have_lcd_driver || missing+=(lcd)
    if ! have_fan_driver; then
        if is_msi_board; then
            missing+=(fan)
        else
            warn "not an MSI board; skipping the nct6687 fan driver (fan control needs a supported board)"
        fi
    fi
    [ ${#missing[@]} -eq 0 ] && return
    echo "Drivers not installed:"
    local d
    for d in "${missing[@]}"; do
        case "$d" in
            lcd) echo "  - cooler LCD (ms91xx-linux-drm)" ;;
            fan) echo "  - fans and pump (nct6687d)" ;;
        esac
    done
    if confirm "Download, build and install them now (uses sudo, apt and DKMS)?"; then
        install_drivers "${missing[@]}"
    else
        warn "skipped; see README for manual driver installation"
    fi
}

install_user() {
    info "Installing program files to $LIBDIR"
    systemctl --user stop "${SERVICES[@]}" 2>/dev/null || true
    mkdir -p "$LIBDIR" "$UNITDIR" "$APPDIR" "$ICONDIR"
    install -m 644 "$REPO"/src/*.py "$LIBDIR"/
    rm -rf "$LIBDIR/__pycache__" "$LIBDIR/locale"
    for po in "$REPO"/po/*.po; do
        lang=$(basename "$po" .po)
        mkdir -p "$LIBDIR/locale/$lang/LC_MESSAGES"
        msgfmt -o "$LIBDIR/locale/$lang/LC_MESSAGES/valkyrie-b240.mo" "$po"
    done

    info "Installing systemd user services"
    for s in "${SERVICES[@]}"; do
        rm -f "$UNITDIR/$s"
        install -m 644 "$REPO/data/$s" "$UNITDIR/$s"
    done
    systemctl --user daemon-reload
    systemctl --user enable "${SERVICES[@]}"
    systemctl --user restart "${SERVICES[@]}"

    info "Installing launcher and icon"
    rm -f "$APPDIR/$APP_ID.desktop"
    sed "s|@LIBDIR@|$LIBDIR|g" "$REPO/data/$APP_ID.desktop.in" > "$APPDIR/$APP_ID.desktop"
    install -m 644 "$REPO/data/icons/$APP_ID.svg" "$ICONDIR/$APP_ID.svg"
    update-desktop-database "$APPDIR" 2>/dev/null || true
    touch "$HOME/.local/share/icons/hicolor"
}

install_system() {
    info "Installing udev rules (needs sudo)"
    sed "s|@GROUP@|$GROUP|g" "$REPO/data/72-valkyrie-lcd.rules.in" | sudo tee "$LCD_RULE" >/dev/null
    /usr/bin/python3 -c "import sys; sys.path.insert(0, '$LIBDIR'); import cooler_common as c; \
print(c.udev_rule('$GROUP'), end='')" | sudo tee "$FAN_RULE" >/dev/null
    sudo udevadm control --reload
    sudo udevadm trigger --action=add --subsystem-match=hwmon --attr-match=name=nct6687 || true
    echo
    echo "Reboot once so the desktop releases the cooler LCD and valkyrie-lcd can drive it."
}

uninstall() {
    info "Stopping services and handing fans back to BIOS"
    systemctl --user disable --now "${SERVICES[@]}" 2>/dev/null || true
    [ -f "$LIBDIR/valkyrie_fand.py" ] && /usr/bin/python3 "$LIBDIR/valkyrie_fand.py" --restore || true
    for s in "${SERVICES[@]}"; do rm -f "$UNITDIR/$s"; done
    systemctl --user daemon-reload
    rm -rf "$LIBDIR"
    rm -f "$APPDIR/$APP_ID.desktop" "$ICONDIR/$APP_ID.svg"
    update-desktop-database "$APPDIR" 2>/dev/null || true
    if [ -f "$LCD_RULE" ] || [ -f "$FAN_RULE" ]; then
        info "Removing udev rules (needs sudo)"
        sudo rm -f "$LCD_RULE" "$FAN_RULE"
        sudo udevadm control --reload
    fi
    echo "Settings in ~/.config/valkyrie-cooler were kept; delete that folder to remove them too."
    echo "The kernel drivers were kept. To remove them as well:"
    echo "  sudo dkms remove msdisp/<version> --all   # see: dkms status"
    echo "  sudo dkms remove nct6687d/1 --all"
    echo "  sudo rm /etc/modules-load.d/nct6687.conf /etc/modprobe.d/nct6683-blacklist.conf"
}

case "${1:-}" in
    "") check_deps interactive; ensure_drivers; install_user; install_system ;;
    --drivers)
        drivers=(lcd)
        if is_msi_board; then drivers+=(fan); else warn "not an MSI board; skipping the nct6687 fan driver"; fi
        install_drivers "${drivers[@]}"
        echo "Reboot to load the updated drivers." ;;
    --user-only) check_deps; install_user ;;
    --uninstall) uninstall ;;
    -h | --help) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
