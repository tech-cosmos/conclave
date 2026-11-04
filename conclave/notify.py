"""Pings the Pope: only for wins, decisions and "done"."""

from __future__ import annotations

import platform
import shlex
import shutil
import subprocess
import urllib.request

from conclave.config import Config
from conclave.workspace import board_append, utcnow


def ping(cfg: Config, message: str, sender: str = "dean") -> str:
    board_append(cfg, "Pings", f"{utcnow()} {sender}: {message}")
    how = cfg.pope.notify
    title = f"Conclave {cfg.name}"
    try:
        if how == "none":
            return "logged to COORD.md (notify = none)"
        if how.startswith("ntfy:"):
            topic = how.split(":", 1)[1]
            url = topic if topic.startswith("http") else f"https://ntfy.sh/{topic}"
            req = urllib.request.Request(url, message.encode(), {"Title": title})
            urllib.request.urlopen(req, timeout=15).close()
            return f"sent to {url}"
        if how.startswith("command:"):
            cmd = how.split(":", 1)[1].replace("{message}", shlex.quote(message)).replace("{title}", shlex.quote(title))
            subprocess.run(cmd, shell=True, timeout=30, check=True)
            return "sent by command"
        # desktop
        if platform.system() == "Darwin":
            esc = message.replace('"', "'")
            subprocess.run(["osascript", "-e", f'display notification "{esc}" with title "{title}"'], timeout=10)
            return "desktop notification"
        if shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], timeout=10)
            return "desktop notification"
        return "logged to COORD.md (no desktop notifier found)"
    except Exception as e:  # a failed ping must never kill a loop
        return f"logged to COORD.md; notify failed: {e}"
