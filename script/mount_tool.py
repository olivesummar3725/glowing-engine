#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mount_tool_final.py
Final improved storage browser:
- Interactive UI as normal user, elevate individual commands with sudo
- Wayland/X11/Hyprland/kitty/fish-friendly GUI launching
- Headless fallbacks and --no-gui option
- SAFE mode to block mutating operations
- Robust lsblk parsing and basic LUKS mapper detection
- Logging
"""

from __future__ import annotations
import os
import sys
import json
import subprocess
import termios
import tty
import signal
import shutil
import time
import re
import argparse
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable

# --------------------
# Config / Constants
# --------------------
DEFAULT_MOUNT_BASE = os.environ.get("MOUNT_BASE", "/mnt")
VMKEYS_DIR = Path(os.environ.get("VMKEYS_DIR", "/etc/vmkeys"))
LOG_PATH = Path(os.environ.get("MOUNT_TOOL_LOG", Path.home() / ".cache" / "mount_tool.log"))

_USE_COLORS = sys.stdout.isatty()
COL = {
    "reset": "\033[0m" if _USE_COLORS else "",
    "bold": "\033[1m" if _USE_COLORS else "",
    "underline": "\033[4m" if _USE_COLORS else "",
    "rev": "\033[7m" if _USE_COLORS else "",
    "green": "\033[32m" if _USE_COLORS else "",
    "yellow": "\033[33m" if _USE_COLORS else "",
    "red": "\033[31m" if _USE_COLORS else "",
    "cyan": "\033[36m" if _USE_COLORS else "",
    "magenta": "\033[35m" if _USE_COLORS else "",
    "blue": "\033[34m" if _USE_COLORS else "",
}

BANNER = r"""
███╗   ███╗ ██████╗ ██╗   ██╗███╗   ██╗████████╗
████╗ ████║██╔═══██╗██║   ██║████╗  ██║╚══██╔══╝
██╔████╔██║██║   ██║██║   ██║██╔██╗ ██║   ██║
██║╚██╔╝██║██║   ██║██║   ██║██║╚██╗██║   ██║
██║ ╚═╝ ██║╚██████╔╝╚██████╔╝██║ ╚████║   ██║
╚═╝     ╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝   ╚═╝
"""

# --------------------
# Logging
# --------------------
try:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(LOG_PATH), level=logging.DEBUG,
                        format="%(asctime)s %(levelname)s %(message)s")
except Exception:
    logging.basicConfig(level=logging.DEBUG)

# --------------------
# Global runtime flags (set in main)
# --------------------
SAFE_MODE = False
NO_GUI = False

# --------------------
# Utilities
# --------------------
def run_cmd(cmd: List[str], check=True, capture=True, text=True, env=None) -> subprocess.CompletedProcess:
    """Run a command and return CompletedProcess. Logs errors."""
    logging.debug("run_cmd: %s", " ".join(cmd))
    try:
        return subprocess.run(cmd, check=check, capture_output=capture, text=text, env=env)
    except subprocess.CalledProcessError as e:
        logging.warning("Command failed: %s rc=%s stderr=%s", cmd, e.returncode, e.stderr)
        raise

def run_priv(cmd: List[str], check=True, capture=True, text=True, sudo_prompt=True) -> subprocess.CompletedProcess:
    """
    Run a privileged command via sudo if not root.
    If already root, runs directly.
    """
    if os.geteuid() == 0:
        return run_cmd(cmd, check=check, capture=capture, text=text)
    sudo_cmd = ["sudo"]
    # preserve environment variables that matter for cryptsetup/fsck etc.
    sudo_cmd.extend(cmd)
    return run_cmd(sudo_cmd, check=check, capture=capture, text=text)

def ok() -> str:
    return f"{COL['green']}OK{COL['reset']}"

def err() -> str:
    return f"{COL['red']}ERR{COL['reset']}"

def clear_screen():
    if sys.stdout.isatty():
        os.system("clear")

def safe_input(prompt=""):
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        return None

def confirm(prompt: str, default_no=True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    ans = safe_input(COL["yellow"] + prompt + suffix + COL["reset"])
    if ans is None:
        return False
    ans = ans.strip().lower()
    if not ans:
        return not default_no
    return ans in ("y", "yes")

def get_key() -> str:
    """Single-key read; fallback to line read if no TTY."""
    if not sys.stdin.isatty():
        line = safe_input("")
        if not line:
            return ""
        ch = line[0]
        if ch == "k": return "up"
        if ch == "j": return "down"
        if ch == "\r" or ch == "\n": return "enter"
        return ch
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":  # escape seq
            seq1 = sys.stdin.read(1)
            if seq1 == "[":
                seq2 = sys.stdin.read(1)
                return {"A":"up","B":"down","C":"right","D":"left"}.get(seq2, "esc")
            return "esc"
        if ch.lower() == "h": return "left"
        if ch.lower() == "j": return "down"
        if ch.lower() == "k": return "up"
        if ch.lower() == "l": return "right"
        if ch == "\r": return "enter"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

def df_usage(mountpoint: str) -> str:
    if not mountpoint or mountpoint == "-":
        return "-"
    try:
        out = run_cmd(["df", "-h", mountpoint], capture=True).stdout.strip().splitlines()
        if len(out) >= 2:
            parts = out[1].split()
            return parts[4] if len(parts) >= 5 else "ERR"
    except Exception:
        return "ERR"
    return "ERR"

def color_usage(usage: str) -> str:
    if usage.endswith("%"):
        try:
            v = int(usage.rstrip("%"))
            if v < 70: return f"{COL['green']}{usage}{COL['reset']}"
            if v < 90: return f"{COL['yellow']}{usage}{COL['reset']}"
            return f"{COL['red']}{usage}{COL['reset']}"
        except Exception:
            pass
    if usage == "ERR":
        return f"{COL['red']}ERR{COL['reset']}"
    return usage

def parent_disk(name: str) -> str:
    # nvme0n1p3 -> nvme0n1 ; mmcblk0p1 -> mmcblk0 ; sda3 -> sda
    if re.match(r"^(nvme\d+n\d+p)\d+$", name):
        return re.sub(r"p\d+$", "", name)
    if re.match(r"^mmcblk\d+p\d+$", name):
        return re.sub(r"p\d+$", "", name)
    return re.sub(r"\d+$", "", name)

# --------------------
# Data
# --------------------
@dataclass
class Partition:
    name: str
    size: str
    fstype: str
    mountpoints: List[str] = field(default_factory=list)
    label: Optional[str] = None
    uuid: Optional[str] = None
    type: Optional[str] = None
    is_swap: bool = False
    is_luks: bool = False
    luks_mapper: Optional[str] = None
    luks_unlocked: bool = False

    @property
    def dev(self) -> str:
        return f"/dev/{self.name}"

    @property
    def mount(self) -> str:
        mps = [m for m in (self.mountpoints or []) if m]
        return mps[0] if mps else "-"

# --------------------
# lsblk parsing
# --------------------
def fetch_devices() -> List[Partition]:
    try:
        out = run_cmd(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS"], capture=True).stdout
    except Exception:
        logging.exception("lsblk failed")
        return []
    try:
        raw = json.loads(out).get("blockdevices", [])
    except Exception:
        logging.exception("lsblk JSON parse failed")
        return []
    parts: List[Partition] = []

    def walk(node: Dict[str, Any]):
        name = node.get("name", "")
        size = node.get("size", "")
        typ = node.get("type", "")
        fs = node.get("fstype") or ""
        label = node.get("label")
        uuid = node.get("uuid")
        mps = node.get("mountpoints") or []

        is_swap = (fs == "swap")
        is_luks = False
        mapper = None
        unlocked = False

        if typ == "crypt":
            is_luks = True
            mapper = name
            unlocked = True
        elif fs == "crypto_LUKS":
            is_luks = True
            unlocked = False

        if typ in ("part", "crypt", "lvm") or (typ and typ != "disk"):
            p = Partition(name=name, size=size, fstype=fs, mountpoints=mps,
                          label=label, uuid=uuid, type=typ, is_swap=is_swap,
                          is_luks=is_luks, luks_mapper=mapper, luks_unlocked=unlocked)
            parts.append(p)
        for ch in (node.get("children") or []):
            walk(ch)

    for dev in raw:
        walk(dev)

    # link parents to child mappers heuristically
    for p in parts:
        if p.is_luks and not p.luks_unlocked:
            for cand in parts:
                if cand.type == "crypt" and cand.luks_unlocked:
                    if cand.name.startswith(p.name) or (p.uuid and p.uuid in cand.name):
                        p.luks_unlocked = True
                        p.luks_mapper = cand.name
                        break
    return parts

# --------------------
# UI helpers
# --------------------
def pad(s: str, w: int) -> str:
    return (s or "").ljust(w)[:w]

def shorten(s: str, w: int) -> str:
    if not s:
        return ""
    if len(s) <= w:
        return s
    return s[: max(0, w - 1)] + "…"

def format_row(p: Partition, sel=False, mount_w=22) -> str:
    usage = color_usage(df_usage(p.mount))
    fs = p.fstype or "-"
    label = p.label or p.uuid or "-"
    status = []
    if p.is_swap:
        status.append("SWAP")
    if p.is_luks and p.luks_unlocked:
        status.append(f"LUKS:UNLOCKED({p.luks_mapper or '-'})")
    elif p.is_luks:
        status.append("LUKS:LOCKED")
    cols = [
        pad(p.name, 14),
        pad(p.size, 8),
        pad(fs, 10),
        pad(shorten(label, 20), 20),
        pad(shorten(p.mount, mount_w), mount_w),
        pad(shorten("/".join(status) if status else "-", 28), 28),
        pad(usage, 6),
    ]
    line = " ".join(cols)
    return (COL["rev"] + line + COL["reset"]) if sel else line

def draw_list(devs: List[Partition], selected: int):
    clear_screen()
    if sys.stdout.isatty():
        print(COL["magenta"] + BANNER + COL["reset"])
    title = "Storage Browser  •  q=quit  r=reload  arrows/hjkl=move  Enter=details  x=open  t=terminal"
    if SAFE_MODE:
        title += "  " + COL["yellow"] + "[SAFE MODE]" + COL["reset"]
    if NO_GUI:
        title += "  " + COL["yellow"] + "[NO-GUI]" + COL["reset"]
    print(COL["bold"] + title + COL["reset"])
    max_mount = max([len(p.mount) for p in devs] + [5])
    mount_w = min(max_mount + 2, 30)
    header = " ".join([
        pad("Device",14), pad("Size",8), pad("FSType",10),
        pad("Label/UUID",20), pad("Mount",mount_w),
        pad("Status",28), pad("Use%",6)
    ])
    print(COL["underline"] + header + COL["reset"])
    for i, p in enumerate(devs):
        print(format_row(p, i == selected, mount_w))

# --------------------
# Safety decorator
# --------------------
def block_if_safe(func: Callable) -> Callable:
    def wrapper(*a, **kw):
        if SAFE_MODE:
            print(f"{COL['yellow']}[SAFE MODE] {func.__name__} blocked{COL['reset']}")
            logging.info("SAFE_MODE blocked %s", func.__name__)
            return None
        return func(*a, **kw)
    wrapper.__name__ = func.__name__
    return wrapper

# --------------------
# GUI and terminal launching (works on Wayland/X11/hybrid)
# --------------------
def has_display() -> bool:
    if NO_GUI:
        return False
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

def run_as_original_user(cmd: List[str]):
    """
    When the script is running under sudo, call command as original user preserving
    relevant env variables (DISPLAY, XAUTHORITY, WAYLAND_DISPLAY).
    If not under sudo, just run subprocess.
    """
    sudo_user = os.environ.get("SUDO_USER")
    env = os.environ.copy()
    # minimal env pass-through for display
    for k in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
        if k in os.environ:
            env[k] = os.environ[k]
    if sudo_user:
        su_cmd = ["sudo", "-u", sudo_user, "env"]
        # pass through the display environment entries explicitly
        for k in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET"):
            if k in env and env[k]:
                su_cmd.append(f"{k}={env[k]}")
        su_cmd.extend(cmd)
        try:
            subprocess.Popen(su_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            logging.exception("run_as_original_user failed with sudo -u")
            return False
    else:
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
            return True
        except Exception:
            logging.exception("run_as_original_user failed")
            return False

def open_file_manager(path: str):
    if not path or path == "-":
        print(f"{COL['red']}[!] Not mounted.{COL['reset']}")
        return
    if not has_display():
        # headless fallback: try terminal file manager
        for cmd in ("ranger", "mc", "nnn"):
            if shutil.which(cmd):
                try:
                    os.execvp(cmd, [cmd, path])
                except Exception:
                    continue
        print(f"{COL['yellow']}No GUI and no terminal file manager found. Path: {path}{COL['reset']}")
        return
    # prefer xdg-open, but run as original user if necessary
    if shutil.which("xdg-open"):
        if run_as_original_user(["xdg-open", path]):
            return
    # try popular GUIs
    for gui in ("thunar", "nautilus", "pcmanfm", "dolphin"):
        if shutil.which(gui):
            if run_as_original_user([gui, path]):
                return
    print(f"{COL['yellow']}Could not open file manager for {path}{COL['reset']}")

def open_terminal(path: str):
    if not path or path == "-":
        print(f"{COL['red']}[!] Not mounted.{COL['reset']}")
        return
    if has_display():
        # prefer terminals known to accept working dir
        gui_terms = [
            ("kitty", ["kitty", "--directory", path]),
            ("wezterm", ["wezterm", "start", "--cwd", path]),
            ("gnome-terminal", ["gnome-terminal", "--", os.environ.get("SHELL", "/bin/sh"), "-c", f"cd '{path}' && exec {os.environ.get('SHELL','/bin/sh')}"]),
            ("konsole", ["konsole", "--workdir", path]),
            ("alacritty", ["alacritty", "--working-directory", path]),
            ("xfce4-terminal", ["xfce4-terminal", "--working-directory", path]),
            ("foot", ["foot", "start", "--working-directory", path])  # foot variants differ per install
        ]
        for name, cmd in gui_terms:
            if shutil.which(name):
                if run_as_original_user(cmd):
                    return
        # fallback to xdg-open as user
        if shutil.which("xdg-open"):
            if run_as_original_user(["xdg-open", path]):
                return
        print(f"{COL['yellow']}No GUI terminal found to open at {path}{COL['reset']}")
    else:
        # headless: try terminal-based file manager or launch shell
        for cmd in ("ranger", "mc", "nnn", "bash", "sh"):
            if shutil.which(cmd):
                try:
                    os.execvp(cmd, [cmd, path] if cmd in ("ranger","mc","nnn") else [cmd])
                except Exception:
                    continue
        print(f"{COL['yellow']}No terminal file manager found. Please cd to {path} manually.{COL['reset']}")

# --------------------
# Filesystem actions (privileged)
# --------------------
def choose_mount_point(p: Partition) -> str:
    base = DEFAULT_MOUNT_BASE
    name = p.label or p.name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    default = f"{base}/{name}"
    ans = safe_input(COL["cyan"] + f"Mount point [{default}]: " + COL["reset"])
    if ans is None or ans.strip() == "":
        ans = default
    Path(ans).mkdir(parents=True, exist_ok=True)
    return ans

def kill_processes_using(p: Partition) -> bool:
    dev_path = p.dev if not (p.is_luks and p.luks_unlocked and p.luks_mapper) else f"/dev/mapper/{p.luks_mapper}"
    if not shutil.which("lsof"):
        print(f"{COL['yellow']}lsof not available, cannot detect processes.{COL['reset']}")
        return True
    try:
        res = run_cmd(["lsof", "-t", dev_path], capture=True)
        pids = [x for x in res.stdout.strip().split() if x.isdigit()]
    except Exception:
        pids = []
    if not pids:
        return True
    print(f"{COL['yellow']}Processes using {dev_path}: {', '.join(pids)}{COL['reset']}")
    if not confirm("Kill these processes?", default_no=True):
        return False
    ok_all = True
    for pid in pids:
        try:
            run_priv(["kill", "-TERM", pid], check=False)
        except Exception:
            ok_all = False
    time.sleep(0.5)
    return ok_all

@block_if_safe
def do_mount(p: Partition):
    if p.mount and p.mount != "-":
        print(f"{COL['yellow']}Already mounted at {p.mount}{COL['reset']}")
        return
    mp = choose_mount_point(p)
    dev_path = p.dev
    if p.is_luks and p.luks_unlocked and p.luks_mapper:
        dev_path = f"/dev/mapper/{p.luks_mapper}"
    try:
        run_priv(["mount", dev_path, mp])
        print(f"{ok()} Mounted {dev_path} at {mp}")
    except Exception as e:
        print(f"{err()} mount failed: {e}")

@block_if_safe
def do_unmount(p: Partition):
    if not p.mount or p.mount == "-":
        print(f"{COL['yellow']}Not mounted.{COL['reset']}")
        return
    if not confirm(f"Unmount {p.dev} from {p.mount}?"):
        return
    dev_path = p.dev if not (p.is_luks and p.luks_unlocked and p.luks_mapper) else f"/dev/mapper/{p.luks_mapper}"
    try:
        run_priv(["umount", dev_path])
        print(f"{ok()} Unmounted {dev_path}")
    except Exception:
        print(f"{COL['yellow']}umount failed, attempting to kill processes and lazy-unmount...{COL['reset']}")
        if kill_processes_using(p):
            try:
                run_priv(["umount", dev_path])
                print(f"{ok()} Unmounted after killing processes")
                return
            except Exception:
                pass
        try:
            run_priv(["umount", "-l", dev_path], check=False)
            print(f"{ok()} Lazy unmount attempted for {dev_path}")
        except Exception as e:
            print(f"{err()} Still can't unmount: {e}")

@block_if_safe
def do_remount(p: Partition):
    dev_path = p.dev if not (p.is_luks and p.luks_unlocked and p.luks_mapper) else f"/dev/mapper/{p.luks_mapper}"
    mount_at = p.mount if p.mount and p.mount != "-" else choose_mount_point(p)
    try:
        run_priv(["umount", dev_path], check=False)
        run_priv(["mount", dev_path, mount_at])
        print(f"{ok()} Remounted {dev_path} at {mount_at}")
    except Exception as e:
        print(f"{err()} remount failed: {e}")

@block_if_safe
def do_fsck(p: Partition):
    dev_path = p.dev if not (p.is_luks and p.luks_unlocked and p.luks_mapper) else f"/dev/mapper/{p.luks_mapper}"
    if p.mount and p.mount != "-":
        if not confirm(f"{dev_path} is mounted at {p.mount}. Unmount to run fsck?"):
            return
        try:
            run_priv(["umount", dev_path])
        except Exception:
            if not kill_processes_using(p):
                print(f"{err()} aborting fsck")
                return
            run_priv(["umount", dev_path], check=False)
    try:
        print(COL["cyan"] + f"Running fsck on {dev_path} ..." + COL["reset"])
        run_priv(["fsck", "-y", dev_path])
        print(ok(), "Filesystem check completed")
    except Exception as e:
        print(f"{err()} fsck failed: {e}")

@block_if_safe
def do_eject(p: Partition):
    base = parent_disk(p.name)
    whole = f"/dev/{base}"
    if not confirm(f"Power off (eject) whole device {whole}?"):
        return
    if p.mount and p.mount != "-":
        if not confirm(f"{p.dev} is mounted at {p.mount}. Unmount first?", default_no=False):
            return
        do_unmount(p)
    try:
        run_priv(["udisksctl", "power-off", "-b", whole])
        print(f"{ok()} Ejected {whole}")
    except Exception as e:
        print(f"{err()} eject failed: {e}")

@block_if_safe
def do_swap_toggle(p: Partition):
    if not p.is_swap:
        print(f"{COL['yellow']}Not a swap device.{COL['reset']}")
        return
    dev_path = p.dev
    try:
        out = run_cmd(["swapon", "--show=NAME"], capture=True).stdout
    except Exception:
        out = ""
    active = any(dev_path in line for line in out.splitlines())
    if active:
        if not confirm(f"Disable swap on {dev_path}?"):
            return
        try:
            run_priv(["swapoff", dev_path])
            print(f"{ok()} swapoff {dev_path}")
        except Exception as e:
            print(f"{COL['yellow']}swapoff failed: {e}{COL['reset']}")
    else:
        if not confirm(f"Enable swap on {dev_path}?", default_no=False):
            return
        try:
            run_priv(["swapon", dev_path])
            print(f"{ok()} swapon {dev_path}")
        except Exception as e:
            print(f"{err()} swapon failed: {e}")

@block_if_safe
def luks_unlock_passphrase(p: Partition):
    if not p.is_luks or p.luks_unlocked:
        print(f"{COL['yellow']}Not a locked LUKS partition.{COL['reset']}")
        return
    mapper = safe_input(COL['cyan'] + f"Mapper name [{p.name}]: " + COL['reset']) or p.name
    try:
        run_priv(["cryptsetup", "open", "--type", "luks", p.dev, mapper])
        print(f"{ok()} Unlocked {p.dev} → /dev/mapper/{mapper}")
    except Exception as e:
        print(f"{err()} cryptsetup open failed: {e}")

@block_if_safe
def luks_unlock_keyfile(p: Partition):
    if not p.is_luks or p.luks_unlocked:
        print(f"{COL['yellow']}Not a locked LUKS partition.{COL['reset']}")
        return
    candidates = []
    if p.uuid: candidates.append(VMKEYS_DIR / f"{p.uuid}.key")
    if p.label: candidates.append(VMKEYS_DIR / f"{p.label}.key")
    candidates.append(VMKEYS_DIR / f"{p.name}.key")
    keyfile = None
    for c in candidates:
        if c.is_file():
            keyfile = str(c)
            break
    if not keyfile:
        keyfile = safe_input(COL["cyan"] + f"Keyfile path (or Enter to cancel): " + COL["reset"])
        if not keyfile:
            print(COL["yellow"] + "Cancelled." + COL["reset"])
            return
        if not Path(keyfile).is_file():
            print(COL['red'] + "Keyfile not found." + COL['reset'])
            return
    mapper = safe_input(COL['cyan'] + f"Mapper name [{p.name}]: " + COL['reset']) or p.name
    try:
        run_priv(["cryptsetup", "open", "--type", "luks", "--key-file", keyfile, p.dev, mapper])
        print(f"{ok()} Unlocked with keyfile: /dev/mapper/{mapper}")
    except Exception as e:
        print(f"{err()} cryptsetup open failed: {e}")

@block_if_safe
def luks_lock(p: Partition):
    if not p.is_luks:
        print(f"{COL['yellow']}Not a LUKS device.{COL['reset']}")
        return
    mapper = p.luks_mapper or p.name
    if p.mount and p.mount != "-":
        if not confirm(f"{p.dev} is mounted at {p.mount}. Unmount first?"):
            return
        do_unmount(p)
    try:
        run_priv(["cryptsetup", "close", mapper])
        print(f"{ok()} Locked /dev/mapper/{mapper}")
    except Exception as e:
        print(f"{err()} cryptsetup close failed: {e}")

# --------------------
# Details UI
# --------------------
def show_details(p: Partition):
    while True:
        clear_screen()
        print(COL["bold"] + f"Details for {p.name}" + COL["reset"])
        print(json.dumps({
            "name": p.name, "size": p.size, "fstype": p.fstype, "mount": p.mount,
            "label": p.label, "uuid": p.uuid, "type": p.type,
            "is_swap": p.is_swap, "is_luks": p.is_luks,
            "luks_mapper": p.luks_mapper, "luks_unlocked": p.luks_unlocked
        }, indent=2))
        print()
        print(COL["cyan"] + "Actions: " + COL["reset"] +
              "[m]ount  [u]nmount  [E]ject  [r]emount  [f]sck  [k]ill-pids  " +
              "[o]unlock  [O]unlock-key  [l]ock  [s]wap  [x]open  [t]erminal  [Enter] return")
        key = get_key()
        if key == "enter" or key in ("esc", "q"):
            return
        elif key == "m": do_mount(p)
        elif key == "u": do_unmount(p)
        elif key == "E": do_eject(p)
        elif key == "r": do_remount(p)
        elif key == "f": do_fsck(p)
        elif key == "k": kill_processes_using(p)
        elif key == "o": luks_unlock_passphrase(p)
        elif key == "O": luks_unlock_keyfile(p)
        elif key == "l": luks_lock(p)
        elif key == "s": do_swap_toggle(p)
        elif key == "x": open_file_manager(p.mount)
        elif key == "t": open_terminal(p.mount)
        time.sleep(0.15)

# --------------------
# Main
# --------------------
def main(argv: Optional[List[str]] = None):
    global SAFE_MODE, NO_GUI
    parser = argparse.ArgumentParser(description="Storage browser")
    parser.add_argument("--safe", action="store_true", help="Do not perform mutating actions")
    parser.add_argument("--no-gui", action="store_true", help="Force terminal-only behavior (no GUI launches)")
    parser.add_argument("--mount-base", type=str, help="Mount base directory (overrides MOUNT_BASE env)")
    args = parser.parse_args(argv or sys.argv[1:])
    SAFE_MODE = SAFE_MODE or args.safe
    NO_GUI = args.no_gui or bool(os.environ.get("MOUNT_TOOL_NO_GUI"))
    if args.mount_base:
        os.environ["MOUNT_BASE"] = args.mount_base

    selected = 0
    devs: List[Partition] = fetch_devices()
    def on_resize(signum, frame):
        draw_list(devs, selected)
    signal.signal(signal.SIGWINCH, on_resize)

    if not devs:
        print(COL['red'] + "No partitions found." + COL['reset'])
        return

    while True:
        draw_list(devs, selected)
        k = get_key()
        if k in ("q", "esc"):
            break
        elif k in ("r",):
            devs = fetch_devices()
            if selected >= len(devs):
                selected = 0
        elif k == "up":
            selected = (selected - 1) % len(devs)
        elif k == "down":
            selected = (selected + 1) % len(devs)
        elif k == "x":
            open_file_manager(devs[selected].mount)
            safe_input("\nPress Enter to continue...")
        elif k == "t":
            open_terminal(devs[selected].mount)
            safe_input("\nPress Enter to continue...")
        elif k == "enter":
            devs = fetch_devices()
            if selected >= len(devs):
                selected = 0
            show_details(devs[selected])

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye!")
        sys.exit(0)
