#!/usr/bin/env python3

import os
import subprocess
import sys
import stat

SCRIPT_DIR = os.path.expanduser("~/glowing-engine/script")

# ANSI color codes
COLORS = {
    'HEADER': '\033[95m',
    'BANNER': '\033[93;1m',
    'OPTION': '\033[92m',
    'PROMPT': '\033[94;1m',
    'ERROR': '\033[91;1m',
    'WARNING': '\033[93m',
    'SUCCESS': '\033[92;1m',
    'SCRIPT': '\033[96m',
    'SUDO': '\033[91m',
    'EXECUTABLE': '\033[92m',
    'NON_EXEC': '\033[93m',
    'RESET': '\033[0m'
}

def clear_screen():
    """Clear terminal screen based on OS"""
    os.system('clear' if os.name == 'posix' else 'cls')

def print_banner():
    """Display professional program banner"""
    print(f"{COLORS['BANNER']}{'=' * 60}{COLORS['RESET']}")
    print(f"{COLORS['BANNER']}{'SCRIPT LAUNCHER'.center(60)}{COLORS['RESET']}")
    print(f"{COLORS['BANNER']}{'=' * 60}{COLORS['RESET']}")
    print(f"{COLORS['HEADER']}{'Universal Script Runner with Sudo Support'.center(60)}{COLORS['RESET']}")
    print(f"{COLORS['HEADER']}{'-' * 60}{COLORS['RESET']}")
    print()

def list_scripts():
    """List available scripts in the script directory"""
    try:
        all_files = os.listdir(SCRIPT_DIR)
        scripts = []
        for f in all_files:
            path = os.path.join(SCRIPT_DIR, f)
            if os.path.isfile(path):
                # Check if file is executable
                executable = os.access(path, os.X_OK)
                scripts.append({
                    'name': f,
                    'path': path,
                    'executable': executable
                })
        return sorted(scripts, key=lambda x: x['name'])
    except FileNotFoundError:
        print(f"\n{COLORS['ERROR']}ERROR: Script directory not found: {SCRIPT_DIR}{COLORS['RESET']}")
        print(f"{COLORS['WARNING']}Please create the directory or update the path in the script.{COLORS['RESET']}")
        sys.exit(1)
    except PermissionError:
        print(f"\n{COLORS['ERROR']}ERROR: Permission denied accessing script directory{COLORS['RESET']}")
        sys.exit(1)

def get_interpreter(script_path):
    """Determine the appropriate interpreter for a script"""
    try:
        with open(script_path, 'r') as f:
            first_line = f.readline().strip()
            if first_line.startswith('#!'):
                return first_line[2:].strip()
    except Exception:
        pass
    
    # Check file extension
    if script_path.endswith('.py'):
        return 'python3'
    elif script_path.endswith('.sh'):
        return 'bash'
    elif script_path.endswith('.pl'):
        return 'perl'
    elif script_path.endswith('.rb'):
        return 'ruby'
    elif script_path.endswith('.js'):
        return 'node'
    return None

def run_script(script_info, use_sudo=False):
    """Execute selected script with optional sudo"""
    script_path = script_info['path']
    script_name = script_info['name']
    
    print(f"\n{COLORS['SCRIPT']}Running {script_name} {'with SUDO' if use_sudo else ''}...{COLORS['RESET']}")
    print(f"{COLORS['SCRIPT']}{'-' * 60}{COLORS['RESET']}")
    
    try:
        if use_sudo:
            command = ['sudo', script_path]
        elif script_info['executable']:
            command = [script_path]
        else:
            # Try to determine interpreter from shebang or extension
            interpreter = get_interpreter(script_path)
            if interpreter:
                command = [interpreter, script_path]
            else:
                print(f"{COLORS['WARNING']}WARNING: No interpreter found, trying to execute directly{COLORS['RESET']}")
                command = [script_path]
        
        result = subprocess.run(command)
        print(f"{COLORS['SCRIPT']}{'-' * 60}{COLORS['RESET']}")
        
        if result.returncode == 0:
            print(f"{COLORS['SUCCESS']}Script completed successfully!{COLORS['RESET']}")
        else:
            print(f"{COLORS['WARNING']}Script completed with exit code: {result.returncode}{COLORS['RESET']}")
    except subprocess.CalledProcessError as e:
        print(f"\n{COLORS['ERROR']}Warning: Script exited with error (code {e.returncode}){COLORS['RESET']}")
    except FileNotFoundError:
        print(f"\n{COLORS['ERROR']}ERROR: Command not found. Ensure the interpreter is installed.{COLORS['RESET']}")
    except Exception as e:
        print(f"\n{COLORS['ERROR']}Unexpected error: {str(e)}{COLORS['RESET']}")

def print_menu(scripts):
    """Display script menu with execution indicators"""
    print(f"{COLORS['HEADER']}Available Scripts:{COLORS['RESET']}")
    print(f"{COLORS['HEADER']}{'-' * 60}{COLORS['RESET']}")
    
    for idx, script in enumerate(scripts, 1):
        color = COLORS['EXECUTABLE'] if script['executable'] else COLORS['NON_EXEC']
        exe_indicator = '*' if script['executable'] else ''
        print(f"{COLORS['OPTION']}{idx:>2}. {script['name']}{exe_indicator}{COLORS['RESET']}")
    
    print(f"\n{COLORS['OPTION']} 0. Exit (or 'q'/'e'){COLORS['RESET']}")
    print(f"{COLORS['HEADER']}{'-' * 60}{COLORS['RESET']}")
    print(f"{COLORS['WARNING']}* = Executable script{COLORS['RESET']}")

def main():
    """Main program loop"""
    # Enable ANSI colors on Windows
    if sys.platform == 'win32':
        os.system('color')
    
    while True:
        clear_screen()
        print_banner()

        scripts = list_scripts()
        if not scripts:
            print(f"{COLORS['WARNING']}WARNING: No scripts found in directory.{COLORS['RESET']}")
            print(f"{COLORS['WARNING']}Add scripts to {SCRIPT_DIR} and restart the launcher.{COLORS['RESET']}")
            sys.exit(1)

        print_menu(scripts)

        try:
            choice = input(f"\n{COLORS['PROMPT']}Select a script to run (0-{len(scripts)} or 'q'/'e' to exit): {COLORS['RESET']}")
            
            # Exit options
            if choice.lower() in ['0', 'q', 'e']:
                print(f"\n{COLORS['SUCCESS']}Exiting...{COLORS['RESET']}")
                break
            
            choice = int(choice)
            if 1 <= choice <= len(scripts):
                script = scripts[choice - 1]
                
                # Ask if sudo is needed
                use_sudo = False
                if os.name == 'posix' and os.geteuid() != 0:
                    sudo_choice = input(f"{COLORS['PROMPT']}Run with sudo? [y/N]: {COLORS['RESET']}").lower()
                    use_sudo = sudo_choice in ['y', 'yes']
                
                run_script(script, use_sudo)
                input(f"\n{COLORS['PROMPT']}Press Enter to return to menu...{COLORS['RESET']}")
            else:
                print(f"{COLORS['ERROR']}Invalid selection: {choice}. Choose 0-{len(scripts)}{COLORS['RESET']}")
                input(f"{COLORS['PROMPT']}Press Enter to try again...{COLORS['RESET']}")
        except ValueError:
            print(f"{COLORS['ERROR']}Please enter a valid number{COLORS['RESET']}")
            input(f"{COLORS['PROMPT']}Press Enter to try again...{COLORS['RESET']}")

if __name__ == "__main__":
    main()
