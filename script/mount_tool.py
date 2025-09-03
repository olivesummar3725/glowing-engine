#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Final Interactive Storage Browser
Features:
- Live refresh after mount/unmount/swap
- LUKS auto-unlock with keyfile
- Swap management
- Per-partition mount points
- Arrow/jk navigation + numbers 1-9
- Terminal/file manager access
- Automatic sudo elevation
- Help screen
"""

from __future__ import annotations
import os, sys, json, subprocess, termios, tty, time, re, shutil
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Any

# -------------------- Config --------------------
DEFAULT_MOUNT_BASE = "/mnt"
VMKEYS_DIR = Path("/etc/vmkeys")
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

# -------------------- Data --------------------
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

# -------------------- Utilities --------------------
def run_cmd(cmd: List[str], check=True, capture=True, text=True, env=None):
    try:
        return subprocess.run(cmd, check=check, capture_output=capture, text=text, env=env)
    except subprocess.CalledProcessError as e:
        return e

def run_priv(cmd: List[str]):
    if os.geteuid() == 0:
        return run_cmd(cmd)
    return run_cmd(["sudo"] + cmd)

def clear_screen():
    if sys.stdout.isatty(): os.system("clear")

def safe_input(prompt=""):
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        return None

def confirm(prompt: str, default_no=True) -> bool:
    suffix = " [y/N]: " if default_no else " [Y/n]: "
    ans = safe_input(COL["yellow"] + prompt + suffix + COL["reset"])
    if ans is None: return False
    ans = ans.strip().lower()
    if not ans: return not default_no
    return ans in ("y", "yes")

def df_usage(mountpoint: str) -> str:
    if not mountpoint or mountpoint == "-":
        return "-"
    try:
        out = run_cmd(["df", "-h", mountpoint]).stdout.strip().splitlines()
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
    if usage == "ERR": return f"{COL['red']}ERR{COL['reset']}"
    return usage

# -------------------- LSBLK Parsing --------------------
def fetch_devices() -> List[Partition]:
    try:
        out = run_cmd(["lsblk", "-J", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS"]).stdout
        raw = json.loads(out).get("blockdevices", [])
    except Exception:
        return []
    parts = []
    def walk(node):
        name = node.get("name","")
        size = node.get("size","")
        typ = node.get("type","")
        fs = node.get("fstype") or ""
        label = node.get("label")
        uuid = node.get("uuid")
        mps = node.get("mountpoints") or []
        is_swap = (fs == "swap")
        is_luks = False
        mapper = None
        unlocked = False
        if typ == "crypt":
            is_luks = True; mapper = name; unlocked=True
        elif fs == "crypto_LUKS": is_luks=True
        if typ in ("part","crypt","lvm") or (typ and typ!="disk"):
            parts.append(Partition(name=name,size=size,fstype=fs,mountpoints=mps,label=label,uuid=uuid,type=typ,is_swap=is_swap,is_luks=is_luks,luks_mapper=mapper,luks_unlocked=unlocked))
        for ch in node.get("children") or []:
            walk(ch)
    for dev in raw: walk(dev)
    return parts

# -------------------- UI --------------------
def pad(s:str,w:int)->str: return (s or "").ljust(w)[:w]
def shorten(s:str,w:int)->str:
    if not s: return ""
    return s if len(s)<=w else s[:max(0,w-1)]+"…"

def format_row(p:Partition, sel=False, mount_w=22)->str:
    usage = color_usage(df_usage(p.mount))
    fs = p.fstype or "-"
    label = p.label or p.uuid or "-"
    status=[]
    if p.is_swap: status.append("SWAP")
    if p.is_luks and p.luks_unlocked: status.append(f"LUKS:UNLOCKED({p.luks_mapper or '-'})")
    elif p.is_luks: status.append("LUKS:LOCKED")
    cols=[pad(p.name,14),pad(p.size,8),pad(fs,10),pad(shorten(label,20),20),pad(shorten(p.mount,mount_w),mount_w),pad(shorten("/".join(status) if status else "-",28),28),pad(usage,6)]
    line=" ".join(cols)
    return (COL["rev"]+line+COL["reset"]) if sel else line

def draw_list(devs:List[Partition], selected:int):
    clear_screen()
    print(COL["magenta"]+BANNER+COL["reset"])
    title="Storage Browser • q=quit r=reload arrows/jk=move Enter=refresh"
    print(COL["bold"]+title+COL["reset"])
    max_mount = max([len(p.mount) for p in devs]+[5])
    mount_w = min(max_mount+2,30)
    header=" ".join([pad("Device",14),pad("Size",8),pad("FSType",10),pad("Label/UUID",20),pad("Mount",mount_w),pad("Status",28),pad("Use%",6)])
    print(COL["underline"]+header+COL["reset"])
    for i,p in enumerate(devs):
        print(format_row(p,i==selected,mount_w))

# -------------------- Key Input --------------------
def get_key():
    fd=sys.stdin.fileno()
    old=termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch=sys.stdin.read(1)
        if ch=="\x1b":
            seq1=sys.stdin.read(1)
            if seq1=="[":
                seq2=sys.stdin.read(1)
                return {"A":"up","B":"down","C":"right","D":"left"}.get(seq2,"esc")
            return "esc"
        if ch.lower() in ("j","k","h","m","u","s","x","t","q","h"): return ch.lower()
        if ch in "123456789": return ch
        if ch=="\r": return "enter"
        return ch
    finally:
        termios.tcsetattr(fd,termios.TCSADRAIN,old)

# -------------------- Filesystem Actions --------------------
LAST_MOUNT_POINTS = {}

def choose_mount_point(p:Partition):
    default = LAST_MOUNT_POINTS.get(p.name,f"{DEFAULT_MOUNT_BASE}/{p.name}")
    ans = safe_input(COL["cyan"]+f"Mount point [{default}]: "+COL["reset"])
    if not ans: ans=default
    Path(ans).mkdir(parents=True,exist_ok=True)
    LAST_MOUNT_POINTS[p.name]=ans
    return ans

def kill_processes_using(dev_path:str):
    if not shutil.which("lsof"): return True
    try:
        res=run_cmd(["lsof","-t",dev_path],capture=True)
        pids=[x for x in res.stdout.strip().split() if x.isdigit()]
    except: pids=[]
    if not pids: return True
    if not confirm(f"Kill processes using {dev_path}?",True): return False
    for pid in pids:
        try: run_priv(["kill","-TERM",pid])
        except: pass
    time.sleep(0.5)
    return True

def do_mount(p:Partition):
    if p.mount!="-": print(COL["yellow"]+f"Already mounted at {p.mount}"+COL["reset"]); return
    mp = choose_mount_point(p)
    dev_path = p.dev
    if p.is_luks and p.luks_unlocked and p.luks_mapper:
        dev_path=f"/dev/mapper/{p.luks_mapper}"
    elif p.is_luks and not p.luks_unlocked:
        # try auto keyfile unlock
        key_candidates=[VMKEYS_DIR/f"{p.uuid}.key",VMKEYS_DIR/f"{p.label}.key",VMKEYS_DIR/f"{p.name}.key"]
        keyfile=None
        for k in key_candidates:
            if k.is_file(): keyfile=str(k); break
        if keyfile:
            mapper=p.name
            try:
                run_priv(["cryptsetup","open","--type","luks","--key-file",keyfile,p.dev,mapper])
                p.luks_unlocked=True; p.luks_mapper=mapper
                dev_path=f"/dev/mapper/{mapper}"
                print(COL["green"]+f"Auto-unlocked LUKS {p.dev} -> /dev/mapper/{mapper}"+COL["reset"])
            except: print(COL["red"]+f"Failed to unlock {p.dev}"+COL["reset"]); return
        else: print(COL["red"]+f"LUKS device {p.dev} locked, no keyfile"+COL["reset"]); return
    if not kill_processes_using(dev_path): return
    try:
        run_priv(["mount",dev_path,mp])
        print(COL["green"]+f"[+] Mounted {dev_path} -> {mp}"+COL["reset"])
    except Exception as e:
        print(COL["red"]+f"Failed mount {dev_path}: {e}"+COL["reset"])

def do_unmount(p:Partition):
    if p.mount=="-": print(COL["yellow"]+f"Already unmounted {p.dev}"+COL["reset"]); return
    if not kill_processes_using(p.dev): return
    try:
        run_priv(["umount",p.mount])
        print(COL["green"]+f"[+] Unmounted {p.dev} from {p.mount}"+COL["reset"])
    except Exception as e:
        print(COL["red"]+f"Failed unmount {p.dev}: {e}"+COL["reset"])

def do_swap_toggle(p:Partition):
    if not p.is_swap: print(COL["yellow"]+f"{p.dev} is not swap"+COL["reset"]); return
    if "SWAP" in (p.mountpoints or []):
        try: run_priv(["swapoff",p.dev]); print(COL["green"]+f"[+] Swap OFF {p.dev}"+COL["reset"])
        except: print(COL["red"]+f"Failed swapoff {p.dev}"+COL["reset"])
    else:
        try: run_priv(["swapon",p.dev]); print(COL["green"]+f"[+] Swap ON {p.dev}"+COL["reset"])
        except: print(COL["red"]+f"Failed swapon {p.dev}"+COL["reset"])

# -------------------- Help --------------------
HELP_TEXT="""
Arrow keys / j,k: Navigate
Numbers 1-9: Jump to device
Enter: Refresh
m: Mount selected
u: Unmount selected
s: Toggle swap
x: Open in file manager (xdg-open)
t: Open terminal at mount point
h: Show this help
q: Quit
"""

def show_help():
    clear_screen()
    print(COL["cyan"]+"HELP:"+COL["reset"])
    print(HELP_TEXT)
    safe_input("Press Enter to return...")

# -------------------- Main --------------------
def main():
    devs=fetch_devices()
    selected=0
    while True:
        draw_list(devs,selected)
        key=get_key()
        if key in ("q","esc"): break
        elif key in ("up","k"): selected=(selected-1)%len(devs)
        elif key in ("down","j"): selected=(selected+1)%len(devs)
        elif key=="m": do_mount(devs[selected]); time.sleep(0.3); devs=fetch_devices()
        elif key=="u": do_unmount(devs[selected]); time.sleep(0.3); devs=fetch_devices()
        elif key=="s": do_swap_toggle(devs[selected]); time.sleep(0.3); devs=fetch_devices()
        elif key=="h": show_help()
        elif key=="x":
            mp=devs[selected].mount
            if mp!="-": run_cmd(["xdg-open",mp],check=False)
        elif key=="t":
            mp=devs[selected].mount
            if mp!="-": run_cmd(["gnome-terminal","--working-directory",mp],check=False)
        elif key in "123456789":
            idx=int(key)-1
            if idx<len(devs): selected=idx
        elif key=="enter":
            devs=fetch_devices()
        else: pass
        time.sleep(0.05)

if __name__=="__main__":
    if os.geteuid()!=0:
        os.execvp("sudo",["sudo"]+["python3"]+sys.argv)
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")
