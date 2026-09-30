# valkyrie-b240-linux

**English** | [简体中文](README.zh-CN.md)

Unofficial Linux (Ubuntu) tools for the **Valkyrie B240** AIO liquid cooler: a system-monitor panel on the cooler's 2" LCD, and a desktop app for pump and fan control.

> **Unofficial project.** Not affiliated with or endorsed by Valkyrie. "Valkyrie" and "B240" are used only to identify the supported hardware.

<img src="data/icons/io.github.yutong_feng.B240Cooler.svg" width="96" alt="icon">

## Features

- **Monitor panel on the cooler LCD**: clock, CPU / GPU temperature and load, pump and fan RPM, RAM and VRAM usage
- **"B240 Cooler Control" desktop app** (GTK4 / libadwaita), shown in the system language (English and Simplified Chinese so far)
  - Pump, radiator fans and the motherboard's six system fan headers each set to BIOS auto, fixed speed, or a temperature curve (drag to edit)
  - Temperature sources: CPU Tctl, CPU CCD, motherboard CPU sensor, NVIDIA GPU, or the hotter of CPU and GPU
  - Live preview of the cooler LCD, refresh interval, temperature color thresholds
- **Safety** (enforced by the background fan service; the config cannot bypass it)
  - Minimum speed: pump 50%, fans 20%
  - Full speed as soon as the CPU or a fan's temperature source reaches the emergency threshold (default 95°C)
  - Fans go back to BIOS control if a temperature cannot be read for 3 seconds, or if the service stops or crashes

## Tested hardware

| Part | Model |
|---|---|
| Cooler | Valkyrie B240 VK (2" IPS LCD; USB `345f:9132`, MacroSilicon MS912C, EDID name `MCVKA08`) |
| Motherboard | MSI B650M GAMING PLUS WIFI (MS-7E24, NCT6687D sensor chip) |
| CPU / GPU | AMD Ryzen 7 9700X / NVIDIA RTX 4070 Ti SUPER |
| OS | Ubuntu 24.04.5 LTS, kernel 7.0, GNOME (X11 session) |

Known limitations:

- Fan and pump control relies on the [nct6687d](https://github.com/Fred78290/nct6687d) driver, so it only works on boards with an NCT6687 chip (mostly MSI).
- CPU temperature comes from `k10temp`, so only AMD CPUs are supported for now.
- GPU temperature comes from `nvidia-smi`; without an NVIDIA GPU it shows as empty and everything else still works.
- Only tested in a GNOME X11 session.

## Installation

```bash
git clone https://github.com/Yutong-Feng/valkyrie-b240-linux.git
cd valkyrie-b240-linux
./install.sh
```

`install.sh` does everything in one go (it asks before each step that needs sudo):

1. Installs missing packages with apt (GTK4 / libadwaita, Pillow, gettext, …)
2. Downloads, builds and installs the two kernel drivers with DKMS, if they are missing (see below)
3. Installs the app to `~/.local/share/valkyrie-b240`, with the UI translations, two systemd user services, the launcher and the icon
4. Installs two udev rules

**Reboot once after installing**; only then does the cooler LCD start showing the panel (see "How it works" below).

### Drivers

| Driver | Purpose | Notes |
|---|---|---|
| [AFSFerreira/ms91xx-linux-drm](https://github.com/AFSFerreira/ms91xx-linux-drm) | Cooler LCD (MacroSilicon MS912C) | The vendor's GPL source, fixed for modern kernels |
| [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) | Fans and pump | MSI boards only; skipped on other boards. The in-kernel `nct6683` can read but not control fans on MSI boards, so it is blacklisted |

Both are pinned to the revisions this project was tested with (see the top of `install.sh`), and DKMS rebuilds them automatically on kernel updates. To rebuild them by hand, e.g. after a major kernel upgrade:

```bash
./install.sh --drivers
```

With Secure Boot enabled, DKMS signs the modules with a MOK key; enroll it in the blue MOK screen on the next boot.

<details>
<summary>Installing the drivers manually</summary>

```bash
sudo apt install dkms git make gcc rsync linux-headers-$(uname -r)

git clone https://github.com/AFSFerreira/ms91xx-linux-drm.git
cd ms91xx-linux-drm
sudo rsync -a --exclude .git ./ /usr/src/msdisp-3.0.3.13/
sudo dkms add msdisp/3.0.3.13
sudo dkms install msdisp/3.0.3.13
cd ..

git clone https://github.com/Fred78290/nct6687d.git
cd nct6687d
make dkms/install
echo nct6687 | sudo tee /etc/modules-load.d/nct6687.conf
echo "blacklist nct6683" | sudo tee /etc/modprobe.d/nct6683-blacklist.conf
```

</details>

## Usage

- Open **"B240 Cooler Control"** from the app grid. Changes apply within about a second; there is no save button.
- Two background services start automatically when you log in:
  - `valkyrie-lcd`: drives the cooler LCD
  - `valkyrie-fan`: applies fan settings; everything defaults to BIOS auto, so fan behaviour is unchanged until you pick another mode

```bash
systemctl --user status valkyrie-lcd valkyrie-fan
journalctl --user -u valkyrie-lcd -n 50
```

Settings are stored in `~/.config/valkyrie-cooler/config.json`.

### Language

The app follows the system language (`LANGUAGE` / `LC_ALL` / `LC_MESSAGES` / `LANG`) and falls back to English when no translation exists. To start it in a specific language:

```bash
LANGUAGE=zh_CN python3 ~/.local/share/valkyrie-b240/valkyrie_gui.py
```

New translations are welcome: copy `po/valkyrie-b240.pot` to `po/<language>.po` (for example `po/ja.po`), fill in the `msgstr` lines, and run `./install.sh` again. After changing UI strings, refresh the template and existing translations with:

```bash
xgettext --language=Python --keyword=_ --from-code=UTF-8 -o po/valkyrie-b240.pot src/valkyrie_gui.py
msgmerge --update po/zh_CN.po po/valkyrie-b240.pot
```

## How it works

### Cooler LCD

- The panel is 320×240, but the cooler only displays a **640×480@60 (VESA)** signal; the 800×600 mode listed in its EDID just shows the built-in logo. It shows only the **top 640×240** of that signal, squeezed to half width. So the program draws a 320×240 panel, stretches it 2× horizontally and places it at the top of the frame.
- If the desktop (X11) treats the cooler as a monitor, X switches **the whole desktop to a software mouse cursor**, because this "GPU" has no hardware cursor, and the pointer flickers whenever windows redraw. `72-valkyrie-lcd.rules` therefore moves the device off seat0 so the desktop ignores it, and `valkyrie-lcd` drives it directly over DRM/KMS. The desktop grabs the device as soon as the driver loads, so a **reboot** is needed once after installing the rule.
- Known driver bug: it copies frames using the size of the mode the hardware was last enabled with (fbdev sets 800×600 at boot) instead of the framebuffer's size. A smaller framebuffer is read out of bounds, the kernel oopses, and the device hangs until reboot. This project allocates the framebuffer for the driver's largest mode (1920×1080) and turns the output off before switching modes. If a modeset still fails, it is not retried until the next boot, so hung processes do not pile up.

### Fans and pump

- Controlled through the hwmon `pwmN` / `pwmN_enable` files exposed by `nct6687d`: the pump is on the `Pump Fan` header, the radiator fans on `CPU Fan`, and case fans on `System Fan #1–6`.
- Writing PWM values needs a udev rule (`99-valkyrie-fan.rules`) that grants these files to your user group. `install.sh` installs it, and the app's "Authorize…" button can add it at any time.

## Troubleshooting

| Symptom | What to do |
|---|---|
| The cooler only shows its logo | Make sure you rebooted once; run `python3 ~/.local/share/valkyrie-b240/valkyrie_lcd.py --probe` to see each step's result |
| The app's LCD status says "Driver error; paused until the next reboot" | A modeset failed this boot and will be retried after a reboot; please open an issue with the output of `sudo dmesg \| grep -i msdisp` |
| Fan speeds show `--` | `nct6687` is not loaded: `lsmod \| grep nct6687` |
| Fans cannot be controlled and a banner asks for authorization | Click "Authorize…", or run `./install.sh` again |
| The CPU hits 95°C under full load | Check whether PBO or 105W mode is enabled in the BIOS; `tools/cpu_load_test.sh` measures full-load power and temperature |

## Uninstall

```bash
./install.sh --uninstall
```

All fans are handed back to BIOS control first. Settings in `~/.config/valkyrie-cooler` and the two kernel drivers are kept; the uninstaller prints the commands to remove the drivers too.

## License

This project is **source-available, not open source**. It is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

- **Noncommercial use is free**: personal use, study, research, hobby projects, and use by charities, schools, public research institutions and government bodies. You may modify and share the code under the same terms.
- **Commercial use requires a separate license**, which may be paid. This includes use by a company, in a product, or bundled with hardware. To ask for one, open an issue or contact [@Yutong-Feng](https://github.com/Yutong-Feng).

Contributions are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md).

The two drivers above are separate third-party projects under GPL-2.0 and are not included in this repository; `install.sh` downloads them from their upstream repositories.
