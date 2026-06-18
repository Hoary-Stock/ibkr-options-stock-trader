"""Startup helper — kill leftover instances of the same script.

Each GUI kills only OLDER instances of its own script (main.py vs
stock_trader.py don't touch each other), so a fresh launch always wins
and the clientId is freed before connecting.
"""

import os

import psutil


def kill_previous_instances(script_path: str) -> int:
    """Terminate python/pythonw processes running the same script from the
    same directory. Returns the number of processes killed."""
    me = os.getpid()
    script = os.path.basename(script_path).lower()
    app_dir = os.path.normcase(os.path.dirname(os.path.abspath(script_path)))

    killed = 0
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if proc.pid == me:
                continue
            name = (proc.info["name"] or "").lower()
            if name not in ("python.exe", "pythonw.exe"):
                continue
            if not _runs_script(proc, script, app_dir):
                continue

            print(f"[STARTUP] Killing leftover instance "
                  f"pid={proc.pid} ({script})", flush=True)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return killed


def _runs_script(proc, script: str, app_dir: str) -> bool:
    """True if proc's command line runs `script` out of `app_dir`."""
    cmdline = proc.info.get("cmdline") or []
    for arg in cmdline:
        if os.path.basename(arg).lower() != script:
            continue
        arg_dir = os.path.dirname(arg)
        if arg_dir:  # full/relative path in the command line
            if os.path.normcase(os.path.abspath(arg_dir)) == app_dir:
                return True
            # Relative path — resolve against the process's own cwd
        try:
            return os.path.normcase(proc.cwd()) == app_dir
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            return False
    return False
