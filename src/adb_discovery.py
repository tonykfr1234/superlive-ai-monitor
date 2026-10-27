"""Bounded local emulator discovery. Never scan a LAN or restart the ADB server."""
import json
import os
from pathlib import Path
import shutil
import subprocess


def run(executable, *args, timeout=5):
    result = subprocess.run([str(executable), *args], capture_output=True, timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('ADB command failed')
    return result.stdout.decode('utf-8', errors='replace')


def emulator_processes():
    """Only executable paths and listening local ports of known emulator processes."""
    if os.name != 'nt':
        return []
    script = r"""$p=@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(MEmu|MEmuHeadless|MuMuPlayer|MuMuVMMHeadless|MuMuNxMain|NemuHeadless|NemuPlayer)\.exe$' }); $ports=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue); @($p | ForEach-Object { @{path=$_.ExecutablePath; ports=@($ports | Where-Object OwningProcess -eq $_.ProcessId | Select-Object -ExpandProperty LocalPort)} }) | ConvertTo-Json -Compress"""
    try:
        data = json.loads(run('powershell.exe', '-NoProfile', '-Command', script, timeout=10) or '[]')
        return data if isinstance(data, list) else [data]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def candidate_paths(config=None, processes=None):
    config = config or {}
    paths = [config.get('adb_path'), shutil.which('adb')]
    relatives = ('Microvirt/MEmu/adb.exe', 'Netease/MuMuPlayer-12.0/shell/adb.exe',
                 'Netease/MuMuPlayerGlobal-12.0/shell/adb.exe', 'Netease/MuMuPlayer/shell/adb.exe',
                 'MuMuPlayer-12.0/shell/adb.exe', 'MuMuPlayerGlobal-12.0/shell/adb.exe')
    for key in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        base = os.environ.get(key)
        if base:
            paths.extend(str(Path(base)/relative) for relative in relatives)
    folders = [Path(p['path']).parent for p in (processes or []) if p.get('path')]
    if os.name == 'nt':
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 0,
                                        winreg.KEY_READ | view) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                                    name = winreg.QueryValueEx(sub, 'DisplayName')[0].casefold()
                                    if any(word in name for word in ('mumu', 'memu', '逍遙')):
                                        folders.append(Path(winreg.QueryValueEx(sub, 'InstallLocation')[0]))
                            except OSError:
                                continue
                except OSError:
                    continue
    for folder in folders:
        for base in (folder, folder.parent):
            paths.extend(str(base/part) for part in ('adb.exe', 'shell/adb.exe', 'adb/adb.exe'))
    return list(dict.fromkeys(str(Path(p)) for p in paths if p and Path(p).is_file()))


def discover(config=None):
    config = config or {}
    processes = emulator_processes()
    paths = candidate_paths(config, processes)
    if not paths:
        raise RuntimeError('找不到 ADB。請安裝並開啟 MuMu／逍遙，或選取 adb.exe。')
    # Known local emulator endpoints plus ports owned by observed emulator processes.
    ports = {21503, 16384, 7555}
    preferred = config.get('serial', '')
    if preferred.startswith(('127.0.0.1:', 'localhost:')):
        value = preferred.rsplit(':', 1)[-1]
        if value.isdigit() and 1 <= int(value) <= 65535:
            ports.add(int(value))
    ports.update(int(port) for process in processes for port in process.get('ports', [])
                 if str(port).isdigit() and 1024 <= int(port) <= 65535)
    for executable in paths:
        try:
            run(executable, 'start-server', timeout=8)
            def devices():
                return [line.split()[0] for line in run(executable, 'devices').splitlines()
                        if len(line.split()) == 2 and line.split()[1] == 'device'
                        and (line.split()[0].startswith(('127.0.0.1:', 'localhost:', 'emulator-'))
                             or line.split()[0] == config.get('serial'))]
            online = devices()
            preferred = config.get('serial', '')
            if preferred and preferred in online:
                return executable, [preferred]
            # Do not replace an explicit remote/USB device with an unrelated emulator.
            if preferred and not preferred.startswith(('127.0.0.1:', 'localhost:', 'emulator-')):
                return executable, []
            for port in sorted(ports)[:24]:
                try:
                    run(executable, 'connect', '127.0.0.1:'+str(port), timeout=1)
                except (OSError, subprocess.SubprocessError, RuntimeError):
                    pass
            online = devices()
            if preferred in online:
                return executable, [preferred]
            if preferred:
                # A saved device is pinned. Reconnect it, never choose another.
                return executable, []
            if online:
                return executable, list(dict.fromkeys(online))
        except (OSError, subprocess.SubprocessError, RuntimeError):
            continue
    return paths[0], []
