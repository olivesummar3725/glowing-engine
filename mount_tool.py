#!/usr/bin/env python3

import os
import sys
import subprocess
import readline
import signal
from pathlib import Path

# Terminal colors
COLORS = {
    "header": "\033[95m", "info": "\033[94m", "success": "\033[92m",
    "error": "\033[91m", "prompt": "\033[93m", "mounted": "\033[96m",
    "bold": "\033[1m", "end": "\033[0m", "swap": "\033[33m"
}

# Global for tab completion
completion_list = []

def print_banner():
    banner = r"""
███╗   ███╗ ██████╗ ██╗   ██╗███╗   ██╗████████╗
████╗ ████║██╔═══██╗██║   ██║████╗  ██║╚══██╔══╝
██╔████╔██║██║   ██║██║   ██║██╔██╗ ██║   ██║   
██║╚██╔╝██║██║   ██║██║   ██║██║╚██╗██║   ██║   
██║ ╚═╝ ██║╚██████╔╝╚██████╔╝██║ ╚████║   ██║   
╚═╝     ╚═╝ ╚═════╝  ╚═════╝ ╚═╝  ╚═══╝   ╚═╝ 
"""
    print(COLORS["header"] + banner + COLORS["end"])

def init_tab_completion():
    def complete(text, state):
        matches = [x for x in completion_list if x.startswith(text)]
        return matches[state] if state < len(matches) else None
    readline.parse_and_bind("tab: complete")
    readline.set_completer(complete)
    readline.set_completer_delims(' \t\n')

def require_root():
    if os.geteuid() != 0:
        print(f"{COLORS['error']}Please run as root/sudo{COLORS['end']}")
        sys.exit(1)

def get_block_devices():
    try:
        result = subprocess.run(
            ["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,LABEL,UUID", "-nP"],
            stdout=subprocess.PIPE, check=True, text=True
        )
    except subprocess.CalledProcessError as e:
        print(f"{COLORS['error']}lsblk failed: {e}{COLORS['end']}")
        sys.exit(1)

    devices = []
    for line in result.stdout.strip().split('\n'):
        if not line: continue
        
        device_info = {}
        for field in line.split():
            if '=' in field:
                key, value = field.split('=', 1)
                device_info[key] = value.strip('"')
        
        name = device_info.get("NAME", "")
        size = device_info.get("SIZE", "")
        dev_type = device_info.get("TYPE", "")
        fstype = device_info.get("FSTYPE", "")
        mountpoint = device_info.get("MOUNTPOINT", "")
        label = device_info.get("LABEL", "")
        uuid = device_info.get("UUID", "")
        
        if dev_type != "part": continue
        
        # Handle special filesystems
        is_swap = fstype == "swap"
        is_luks = fstype == "crypto_LUKS"
        mapper_name = ""
        
        if is_luks:
            try:
                mapper_result = subprocess.run(
                    ["cryptsetup", "status", name],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
                )
                if mapper_result.returncode == 0:
                    for status_line in mapper_result.stdout.split('\n'):
                        if status_line.startswith("  device:"):
                            mapper_name = status_line.split()[-1].split('/')[-1]
            except Exception:
                pass
                
        devices.append({
            "name": name, "size": size, "type": dev_type, "fstype": fstype,
            "mountpoint": mountpoint, "label": label, "uuid": uuid,
            "is_luks": is_luks, "mapper_name": mapper_name, 
            "unlocked": bool(mapper_name), "is_swap": is_swap
        })
        
    return devices

def list_devices(devices):
    print(f"{COLORS['info']}\nAvailable block devices:{COLORS['end']}")
    header = (f"{COLORS['bold']} {'No.':>3}  {'Device':<16} {'Size':>8} {'Type':<8} "
              f"{'Status':<10} {'Label/UUID':<36}  {'Mountpoint':<20}{COLORS['end']}")
    print(header)
    
    for i, dev in enumerate(devices, start=1):
        if dev["is_luks"]:
            if dev["unlocked"]:
                disp_name = f"/dev/mapper/{dev['mapper_name']}"
                status = "UNLOCKED"
            else:
                disp_name = f"/dev/{dev['name']}"
                status = "LOCKED"
        else:
            disp_name = f"/dev/{dev['name']}"
            status = "NORMAL"
        
        # Special formatting for swap and mounted devices
        if dev["is_swap"]:
            mount_str = f"{COLORS['swap']}[SWAP]{COLORS['end']}"
            id_str = f"{COLORS['swap']}{dev['uuid']}{COLORS['end']}"
        else:
            mount_str = (f"{COLORS['mounted']}{dev['mountpoint']}{COLORS['end']}" 
                        if dev["mountpoint"] else "-")
            id_str = dev["label"] if dev["label"] else dev["uuid"]
        
        print(f" {COLORS['bold']}{i:>3}{COLORS['end']}  {COLORS['prompt']}{disp_name:<16}{COLORS['end']} "
              f"{dev['size']:>8} {dev['type']:<8} {status:<10} {id_str:<36}  {mount_str:<20}")

def get_device_choice(devices):
    while True:
        try:
            choice = input(f"\n{COLORS['prompt']}Choose disk (q to quit): {COLORS['end']}").strip().lower()
            if choice == 'q': sys.exit(0)
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(devices):
                    dev = devices[idx]
                    print(f"\n{COLORS['info']}Selected device:{COLORS['end']}")
                    print(f"  Device:    {COLORS['bold']}/dev/{dev['name']}{COLORS['end']}")
                    if dev['is_luks'] and dev['unlocked']:
                        print(f"  Mapper:    {COLORS['bold']}/dev/mapper/{dev['mapper_name']}{COLORS['end']}")
                    print(f"  Size:      {dev['size']}")
                    print(f"  Type:      {dev['type']}")
                    print(f"  Filesystem:{dev['fstype']}")
                    print(f"  Label:     {dev['label'] if dev['label'] else '-'}")
                    print(f"  UUID:      {dev['uuid']}")
                    print(f"  Mounted at:{dev['mountpoint'] if dev['mountpoint'] else '-'}")
                    if dev['is_swap']:
                        print(f"  {COLORS['swap']}SWAP device detected{COLORS['end']}")
                    return dev
            print(f"{COLORS['error']}Invalid selection{COLORS['end']}")
        except KeyboardInterrupt:
            print(f"\n{COLORS['info']}Operation cancelled{COLORS['end']}")
            sys.exit(0)

def kill_processes_using_device(device):
    """Find and kill processes using the device"""
    if device["is_luks"] and device["unlocked"]:
        dev_path = f"/dev/mapper/{device['mapper_name']}"
    else:
        dev_path = f"/dev/{device['name']}"
    
    try:
        # Find processes using the device
        lsof = subprocess.run(
            ["lsof", "-t", dev_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        
        if lsof.returncode == 0 and lsof.stdout.strip():
            pids = lsof.stdout.strip().split()
            print(f"{COLORS['info']}Processes using {dev_path}: {', '.join(pids)}{COLORS['end']}")
            
            confirm = input(f"{COLORS['prompt']}Kill these processes? (y/N): {COLORS['end']}").strip().lower()
            if confirm == 'y':
                for pid in pids:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        print(f"{COLORS['info']}Sent TERM to PID {pid}{COLORS['end']}")
                    except ProcessLookupError:
                        print(f"{COLORS['error']}Process {pid} not found{COLORS['end']}")
                    except Exception as e:
                        print(f"{COLORS['error']}Failed to kill {pid}: {e}{COLORS['end']}")
                return True
            return False
        return True
    except Exception as e:
        print(f"{COLORS['error']}Error checking processes: {e}{COLORS['end']}")
        return False

def handle_swap(device, action):
    """Handle swap operations"""
    dev_path = f"/dev/{device['name']}"
    if action == "on":
        try:
            subprocess.run(["swapon", dev_path], check=True)
            print(f"{COLORS['success']}Enabled swap {dev_path}{COLORS['end']}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"{COLORS['error']}Failed to enable swap: {e}{COLORS['end']}")
            return False
    elif action == "off":
        try:
            subprocess.run(["swapoff", dev_path], check=True)
            print(f"{COLORS['success']}Disabled swap {dev_path}{COLORS['end']}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"{COLORS['error']}Failed to disable swap: {e}{COLORS['end']}")
            if not kill_processes_using_device(device):
                print(f"{COLORS['error']}Could not free swap device{COLORS['end']}")
                return False
            try:
                subprocess.run(["swapoff", dev_path], check=True)
                print(f"{COLORS['success']}Disabled swap after killing processes{COLORS['end']}")
                return True
            except subprocess.CalledProcessError as e:
                print(f"{COLORS['error']}Still can't disable swap: {e}{COLORS['end']}")
                return False
    return False

def handle_normal_device(device):
    """Handle actions for normal (non-LUKS, non-swap) devices"""
    if device["mountpoint"]:
        try:
            action = input(
                f"{COLORS['prompt']}Device is mounted at {device['mountpoint']}. "
                f"(u)nmount, (r)emount, (f)ilesystem check, or (c)ancel? [u/r/f/c]: {COLORS['end']}"
            ).strip().lower()
            
            if action == 'u':
                if device["is_luks"] and device["unlocked"]:
                    dev_path = f"/dev/mapper/{device['mapper_name']}"
                else:
                    dev_path = f"/dev/{device['name']}"
                
                try:
                    subprocess.run(["umount", dev_path], check=True)
                    print(f"{COLORS['success']}Unmounted {dev_path}{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to unmount: {e}{COLORS['end']}")
                    if kill_processes_using_device(device):
                        try:
                            subprocess.run(["umount", dev_path], check=True)
                            print(f"{COLORS['success']}Unmounted after killing processes{COLORS['end']}")
                            return True
                        except subprocess.CalledProcessError as e:
                            print(f"{COLORS['error']}Still can't unmount: {e}{COLORS['end']}")
                            return False
                    return False
            
            elif action == 'r':
                mount_point = device["mountpoint"]
                if device["is_luks"] and device["unlocked"]:
                    dev_path = f"/dev/mapper/{device['mapper_name']}"
                else:
                    dev_path = f"/dev/{device['name']}"
                
                try:
                    subprocess.run(["umount", dev_path], check=True)
                    subprocess.run(["mount", dev_path, mount_point], check=True)
                    print(f"{COLORS['success']}Remounted {dev_path} at {mount_point}{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to remount: {e}{COLORS['end']}")
                    return False
            
            elif action == 'f':
                if device["is_luks"] and device["unlocked"]:
                    dev_path = f"/dev/mapper/{device['mapper_name']}"
                else:
                    dev_path = f"/dev/{device['name']}"
                
                try:
                    print(f"{COLORS['info']}Running filesystem check on {dev_path}{COLORS['end']}")
                    subprocess.run(["umount", dev_path], check=True)
                    subprocess.run(["fsck", "-y", dev_path], check=True)
                    subprocess.run(["mount", dev_path, device["mountpoint"]], check=True)
                    print(f"{COLORS['success']}Filesystem check completed{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Filesystem check failed: {e}{COLORS['end']}")
                    return False
            
            return False
        except KeyboardInterrupt:
            print(f"\n{COLORS['info']}Operation cancelled{COLORS['end']}")
            sys.exit(0)
    else:
        try:
            action = input(
                f"{COLORS['prompt']}Device not mounted. "
                f"(m)ount, (f)ilesystem check, or (c)ancel? [m/f/c]: {COLORS['end']}"
            ).strip().lower()
            
            if action == 'm':
                mount_point = get_mount_point()
                if device["is_luks"] and device["unlocked"]:
                    dev_path = f"/dev/mapper/{device['mapper_name']}"
                else:
                    dev_path = f"/dev/{device['name']}"
                
                try:
                    subprocess.run(["mount", dev_path, mount_point], check=True)
                    print(f"{COLORS['success']}Mounted {dev_path} at {mount_point}{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to mount: {e}{COLORS['end']}")
                    return False
            
            elif action == 'f':
                if device["is_luks"] and device["unlocked"]:
                    dev_path = f"/dev/mapper/{device['mapper_name']}"
                else:
                    dev_path = f"/dev/{device['name']}"
                
                try:
                    print(f"{COLORS['info']}Running filesystem check on {dev_path}{COLORS['end']}")
                    subprocess.run(["fsck", "-y", dev_path], check=True)
                    print(f"{COLORS['success']}Filesystem check completed{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Filesystem check failed: {e}{COLORS['end']}")
                    return False
            
            return False
        except KeyboardInterrupt:
            print(f"\n{COLORS['info']}Operation cancelled{COLORS['end']}")
            sys.exit(0)

def handle_luks_device(device):
    """Handle LUKS encrypted devices"""
    try:
        if device["unlocked"]:
            action = input(
                f"{COLORS['prompt']}LUKS device is unlocked. "
                f"(m)ount, (u)nmount, (l)ock, (r)emount, or (c)hange passphrase? [m/u/l/r/c]: {COLORS['end']}"
            ).strip().lower()
            
            if action == 'm':
                mount_point = get_mount_point()
                dev_path = f"/dev/mapper/{device['mapper_name']}"
                try:
                    subprocess.run(["mount", dev_path, mount_point], check=True)
                    print(f"{COLORS['success']}Mounted {dev_path} at {mount_point}{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to mount: {e}{COLORS['end']}")
                    return False
            
            elif action == 'u':
                dev_path = f"/dev/mapper/{device['mapper_name']}"
                try:
                    subprocess.run(["umount", dev_path], check=True)
                    print(f"{COLORS['success']}Unmounted {dev_path}{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to unmount: {e}{COLORS['end']}")
                    if kill_processes_using_device(device):
                        try:
                            subprocess.run(["umount", dev_path], check=True)
                            print(f"{COLORS['success']}Unmounted after killing processes{COLORS['end']}")
                            return True
                        except subprocess.CalledProcessError as e:
                            print(f"{COLORS['error']}Still can't unmount: {e}{COLORS['end']}")
                            return False
                    return False
            
            elif action == 'l':
                try:
                    subprocess.run(["cryptsetup", "close", device["mapper_name"]], check=True)
                    print(f"{COLORS['success']}Locked LUKS device /dev/mapper/{device['mapper_name']}{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to lock: {e}{COLORS['end']}")
                    return False
            
            elif action == 'r':
                mount_point = device["mountpoint"] if device["mountpoint"] else get_mount_point()
                dev_path = f"/dev/mapper/{device['mapper_name']}"
                try:
                    subprocess.run(["umount", dev_path], check=True)
                    subprocess.run(["mount", dev_path, mount_point], check=True)
                    print(f"{COLORS['success']}Remounted {dev_path} at {mount_point}{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to remount: {e}{COLORS['end']}")
                    return False
            
            elif action == 'c':
                dev_path = f"/dev/{device['name']}"
                try:
                    subprocess.run(["cryptsetup", "luksChangeKey", dev_path], check=True)
                    print(f"{COLORS['success']}Passphrase changed successfully{COLORS['end']}")
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to change passphrase: {e}{COLORS['end']}")
                    return False
            
            return False
        else:
            action = input(
                f"{COLORS['prompt']}LUKS encrypted device. "
                f"(u)nlock, (v)iew info, or (c)ancel? [u/v/c]: {COLORS['end']}"
            ).strip().lower()
            
            if action == 'u':
                mapper_name = input(
                    f"{COLORS['prompt']}Enter mapper name [{device['name']}]: {COLORS['end']}"
                ).strip() or device["name"]
                
                dev_path = f"/dev/{device['name']}"
                try:
                    subprocess.run(["cryptsetup", "open", "--type", "luks", dev_path, mapper_name], check=True)
                    print(f"{COLORS['success']}Unlocked {dev_path} to /dev/mapper/{mapper_name}{COLORS['end']}")
                    
                    mount_choice = input(
                        f"{COLORS['prompt']}Mount the unlocked device? (y/N): {COLORS['end']}"
                    ).strip().lower()
                    
                    if mount_choice == 'y':
                        mount_point = get_mount_point()
                        try:
                            subprocess.run(["mount", f"/dev/mapper/{mapper_name}", mount_point], check=True)
                            print(f"{COLORS['success']}Mounted at {mount_point}{COLORS['end']}")
                        except subprocess.CalledProcessError as e:
                            print(f"{COLORS['error']}Failed to mount: {e}{COLORS['end']}")
                    
                    return True
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to unlock: {e}{COLORS['end']}")
                    return False
            
            elif action == 'v':
                dev_path = f"/dev/{device['name']}"
                try:
                    print(f"\n{COLORS['info']}LUKS information for {dev_path}:{COLORS['end']}")
                    subprocess.run(["cryptsetup", "luksDump", dev_path], check=True)
                    return False
                except subprocess.CalledProcessError as e:
                    print(f"{COLORS['error']}Failed to get LUKS info: {e}{COLORS['end']}")
                    return False
            
            return False
    except KeyboardInterrupt:
        print(f"\n{COLORS['info']}Operation cancelled{COLORS['end']}")
        sys.exit(0)

def get_mount_point(default=""):
    """Get mount point with tab completion"""
    # Populate completion list with common mount points
    global completion_list
    completion_list = [
        "/mnt", "/media", "/mount", "/data",
        "/home", "/var", "/tmp", "/usr"
    ]
    
    # Add existing mount points from /etc/fstab
    try:
        with open("/etc/fstab", "r") as f:
            for line in f:
                if line.strip() and not line.startswith("#"):
                    parts = line.split()
                    if len(parts) >= 2:
                        completion_list.append(parts[1])
    except Exception:
        pass
    
    # Add existing directories under /mnt and /media
    for base in ["/mnt", "/media"]:
        try:
            for entry in os.listdir(base):
                path = os.path.join(base, entry)
                if os.path.isdir(path):
                    completion_list.append(path)
        except Exception:
            pass
    
    completion_list = sorted(list(set(completion_list)))
    
    # Prompt for mount point
    prompt = f"Enter mount point [{default}]: " if default else "Enter mount point (Tab to complete): "
    while True:
        try:
            mount_point = input(COLORS["prompt"] + prompt + COLORS["end"]).strip()
            if not mount_point and default:
                mount_point = default
                break
            if mount_point:
                break
            print(f"{COLORS['error']}No mount point entered{COLORS['end']}")
        except KeyboardInterrupt:
            print(f"\n{COLORS['info']}Operation cancelled{COLORS['end']}")
            sys.exit(0)
    
    # Create directory if needed
    if not os.path.exists(mount_point):
        print(f"{COLORS['info']}Creating mount point {mount_point}{COLORS['end']}")
        try:
            os.makedirs(mount_point, exist_ok=True)
        except Exception as e:
            print(f"{COLORS['error']}Failed to create directory: {e}{COLORS['end']}")
            sys.exit(1)
    
    return mount_point

def handle_device_actions(device):
    """Route to appropriate handler based on device type"""
    if device["is_swap"]:
        if device["mountpoint"]:  # Swap is active
            action = input(
                f"{COLORS['prompt']}Swap is active. (o)ff, (c)ancel? [o/c]: {COLORS['end']}"
            ).strip().lower()
            if action == 'o':
                return handle_swap(device, "off")
        else:
            action = input(
                f"{COLORS['prompt']}Swap is inactive. (o)n, (c)ancel? [o/c]: {COLORS['end']}"
            ).strip().lower()
            if action == 'o':
                return handle_swap(device, "on")
        return False
    elif device["is_luks"]:
        return handle_luks_device(device)
    else:
        return handle_normal_device(device)

def main():
    print_banner()
    require_root()
    init_tab_completion()

    try:
        devices = get_block_devices()
        if not devices:
            print(f"{COLORS['error']}No partitions found{COLORS['end']}")
            sys.exit(1)

        list_devices(devices)
        device = get_device_choice(devices)
        
        if not handle_device_actions(device):
            print(f"{COLORS['info']}No action taken{COLORS['end']}")
            sys.exit(0)
            
    except KeyboardInterrupt:
        print(f"\n{COLORS['info']}Operation cancelled{COLORS['end']}")
        sys.exit(0)

if __name__ == "__main__":
    main()



