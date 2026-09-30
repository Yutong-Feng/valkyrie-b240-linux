# valkyrie-b240-linux

[English](README.md) | **简体中文**

在 Linux（Ubuntu）上使用 **瓦尔基里 Valkyrie B240** 一体式水冷的非官方工具：冷头 2 寸屏幕显示系统监控面板，图形界面调节水泵和风扇转速。

> **非官方项目。** 本项目与瓦尔基里（Valkyrie）没有任何关联，也未获得其认可。"Valkyrie" 和 "B240" 仅用于说明适配的硬件型号。

<img src="data/icons/io.github.yutong_feng.B240Cooler.svg" width="96" alt="icon">

## 功能

- **冷头屏幕监控面板**：时间、CPU / GPU 温度与负载、水泵和风扇转速、内存和显存占用
- **图形化控制应用「B240 水冷控制」**（GTK4 / libadwaita），界面语言跟随系统（目前支持简体中文和英文）
  - 水泵、冷排风扇、主板 6 个系统风扇接口分别设置：BIOS 自动 / 固定转速 / 温控曲线（拖动编辑）
  - 温度来源可选：CPU Tctl、CPU CCD、主板 CPU 传感器、NVIDIA 显卡，或 CPU 与显卡中较高者
  - 冷头屏幕实时预览、刷新间隔、温度颜色阈值
- **安全机制**（由后台调速服务强制执行，配置无法绕过）
  - 转速下限：水泵 50%，风扇 20%
  - CPU 或风扇依据的温度达到紧急阈值（默认 95°C）时立即全速
  - 连续 3 秒读不到温度，或服务退出 / 崩溃时，自动交还 BIOS 控制

## 已测试环境

| 项目 | 型号 |
|---|---|
| 水冷 | 瓦尔基里 B240 VK（带 2 寸 IPS 屏；USB `345f:9132`，MacroSilicon MS912C，EDID 名称 `MCVKA08`） |
| 主板 | MSI B650M GAMING PLUS WIFI（MS-7E24，传感器芯片 NCT6687D） |
| CPU / GPU | AMD Ryzen 7 9700X / NVIDIA RTX 4070 Ti SUPER |
| 系统 | Ubuntu 24.04.5 LTS，内核 7.0，GNOME（X11 会话） |

已知限制：

- 风扇和水泵控制依赖 [nct6687d](https://github.com/Fred78290/nct6687d) 驱动，目前只适用于使用 NCT6687 芯片的主板（主要是 MSI）。
- CPU 温度读取 `k10temp`，目前只支持 AMD CPU。
- 显卡温度通过 `nvidia-smi` 读取，没有 NVIDIA 显卡时显示为空，不影响其他功能。
- 只在 GNOME X11 会话下测试过。

## 安装

```bash
git clone https://github.com/Yutong-Feng/valkyrie-b240-linux.git
cd valkyrie-b240-linux
./install.sh
```

`install.sh` 会一次完成所有步骤（每个需要 sudo 的步骤都会先询问）：

1. 用 apt 安装缺少的软件包（GTK4 / libadwaita、Pillow、gettext 等）
2. 如果缺少驱动，自动下载、编译并用 DKMS 安装两个内核驱动（见下文）
3. 把应用安装到 `~/.local/share/valkyrie-b240`，包括界面翻译、两个 systemd 用户服务、应用启动器和图标
4. 安装两条 udev 规则

**安装后重启一次**，冷头屏幕才会开始显示监控面板（原因见下文「工作原理」）。

### 驱动

| 驱动 | 用途 | 说明 |
|---|---|---|
| [AFSFerreira/ms91xx-linux-drm](https://github.com/AFSFerreira/ms91xx-linux-drm) | 冷头屏幕（MacroSilicon MS912C） | 基于厂商 GPL 源码，适配新内核 |
| [Fred78290/nct6687d](https://github.com/Fred78290/nct6687d) | 风扇和水泵 | 只适用于 MSI 主板，其他主板会自动跳过。内核自带的 `nct6683` 在 MSI 主板上只能读不能调速，所以会屏蔽它 |

两个驱动都固定在本项目测试过的版本（见 `install.sh` 开头），内核更新时 DKMS 会自动重新编译。如果需要手动重新编译，比如内核大版本升级后：

```bash
./install.sh --drivers
```

开启了 Secure Boot 的机器，DKMS 会用 MOK 密钥给驱动签名，下次开机时需要在蓝色的 MOK 界面里登记这个密钥。

<details>
<summary>手动安装驱动</summary>

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

## 使用

- 在应用列表中打开 **「B240 水冷控制」**。改动约 1 秒后生效，无需保存。
- 两个后台服务随用户登录自动启动：
  - `valkyrie-lcd`：驱动冷头屏幕
  - `valkyrie-fan`：按配置调速，默认全部为 BIOS 自动，不改变任何风扇行为

```bash
systemctl --user status valkyrie-lcd valkyrie-fan
journalctl --user -u valkyrie-lcd -n 50
```

配置文件：`~/.config/valkyrie-cooler/config.json`。

### 界面语言

应用按系统语言（`LANGUAGE` / `LC_ALL` / `LC_MESSAGES` / `LANG`）显示界面，没有对应翻译时显示英文。可以临时指定语言启动：

```bash
LANGUAGE=en python3 ~/.local/share/valkyrie-b240/valkyrie_gui.py
```

欢迎贡献新的翻译：复制 `po/valkyrie-b240.pot` 为 `po/<语言代码>.po`（例如 `po/ja.po`），填写 `msgstr` 后重新运行 `./install.sh`。修改界面文字后，用下面的命令更新模板：

```bash
xgettext --language=Python --keyword=_ --from-code=UTF-8 -o po/valkyrie-b240.pot src/valkyrie_gui.py
msgmerge --update po/zh_CN.po po/valkyrie-b240.pot
```

## 工作原理

### 冷头屏幕

- 屏幕实际分辨率是 320×240，但冷头只能显示 **640×480@60（VESA）** 信号，EDID 里列出的 800×600 只会显示内置 logo。冷头只取 640×480 画面的**上方 640×240**，并把横向压缩一半。因此程序先画 320×240 的面板，横向拉伸 2 倍后放在画面顶部。
- 如果让桌面（X11）把冷头当作显示器，X 会因为这块"显卡"不支持硬件光标，把**整个桌面的鼠标切换成软件光标**，鼠标会在窗口重绘时闪烁。所以 `72-valkyrie-lcd.rules` 把这块设备移出 seat0，桌面不再接管它，改由 `valkyrie-lcd` 通过 DRM/KMS 直接驱动。因为桌面在驱动加载时就占用了设备，规则装好后需要**重启一次**。
- 驱动的已知缺陷：它按硬件上一次启用时的模式尺寸（开机时 fbdev 设为 800×600）拷贝帧，而不是按帧缓冲的实际尺寸。帧缓冲比这个尺寸小就会越界读取，导致内核 oops，设备卡死直到重启。本项目按驱动支持的最大模式 1920×1080 分配帧缓冲，并且在切换模式前先关闭输出。万一仍然出错，同一次开机内不会再重试，以免卡死的进程越积越多。

### 风扇与水泵

- 通过 `nct6687d` 暴露的 hwmon `pwmN` / `pwmN_enable` 控制：水泵是 `Pump Fan` 接口，冷排风扇接在 `CPU Fan` 接口，机箱风扇是 `System Fan #1–6`。
- 写入 PWM 需要一条 udev 规则（`99-valkyrie-fan.rules`）把这些文件授权给你的用户组；`install.sh` 会安装它，应用里的「授权…」按钮也可以随时补装。

## 故障排查

| 现象 | 处理 |
|---|---|
| 冷头只显示 logo | 确认已重启过一次；运行 `python3 ~/.local/share/valkyrie-b240/valkyrie_lcd.py --probe` 查看每一步的结果 |
| 应用里冷头状态显示「驱动出错，已暂停，重启后会再试一次」 | 本次开机的模式切换失败了，重启后会再试一次；请附上 `sudo dmesg \| grep -i msdisp` 提交 issue |
| 转速显示 `--` | `nct6687` 没有加载：`lsmod \| grep nct6687` |
| 调不了转速，顶部提示需要授权 | 点「授权…」，或重新运行 `./install.sh` |
| CPU 温度一满载就到 95°C | 检查 BIOS 是否开启了 PBO 或 105W 模式；`tools/cpu_load_test.sh` 可以测满载功耗和温度 |

## 卸载

```bash
./install.sh --uninstall
```

卸载前会先把所有风扇交还给 BIOS 控制。配置文件 `~/.config/valkyrie-cooler` 和两个内核驱动会保留；卸载程序会打印删除驱动的命令。

## 许可证

本项目是**源码可见（source-available）项目，不是开源项目**，采用 [PolyForm Noncommercial License 1.0.0](LICENSE) 许可证。

- **非商业用途免费**：个人使用、学习、研究、业余项目，以及慈善机构、学校、公共研究机构和政府部门的使用。可以在相同条款下修改和分享代码。
- **商业用途需要另外获得授权**，可能需要付费。包括公司使用、用于产品中，或随硬件一起提供等。如需商业授权，请开 issue 或联系 [@Yutong-Feng](https://github.com/Yutong-Feng)。

欢迎贡献代码，请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。

上面两个驱动是独立的第三方项目（GPL-2.0），本仓库不包含它们的代码；`install.sh` 会从它们的上游仓库下载。
