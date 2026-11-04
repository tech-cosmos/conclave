"""A Cardinal's session loop (the open version of night_agent.sh).

Each session is a fresh, headless agent run that reads its briefs from disk, does ONE cycle of work,
writes notes, commits and stops. The loop restarts it until a STOP flag appears. Memory lives in
files and git, never in a chat context, so a Cardinal can run for days.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from conclave import runners
from conclave.config import Config, Member
from conclave.workspace import ensure_worktree, git, has_remote, lane_file, scaffold, utcnow

SESSION_PROMPT = (
    'You are Cardinal "{name}" in a Conclave (a managed agent team). Read these, in order:\n'
    "1. {workdir}/CONCLAVE.md (the rules for every Cardinal)\n"
    "2. {lane} (your lane; it overrides CONCLAVE.md where they differ)\n"
    "3. the Pope's decisions at the top of {workdir}/COORD.md, in full\n"
    "4. STATE-{name}.md in your working directory (create it if missing)\n"
    "Then do the next cycle of work as they describe, commit with measured numbers, update your notes, and stop.\n"
    "It is {now} (UTC). This is unattended session {i}."
)


def stop_flags(cfg: Config, name: str) -> list[Path]:
    return [cfg.workdir / "STOP", cfg.workdir / f"STOP-{name}"]


def stopped(cfg: Config, name: str) -> bool:
    return any(p.exists() for p in stop_flags(cfg, name))


def state_path(cfg: Config, name: str) -> Path:
    return cfg.workdir / "run" / f"{name}.json"


def pid_path(cfg: Config, name: str) -> Path:
    return cfg.workdir / "run" / f"{name}.pid"


def read_state(cfg: Config, name: str) -> dict:
    try:
        return json.loads(state_path(cfg, name).read_text())
    except (OSError, ValueError):
        return {}


def alive(cfg: Config, name: str) -> int | None:
    try:
        pid = int(pid_path(cfg, name).read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _log(cfg: Config, name: str, msg: str) -> None:
    with open(cfg.workdir / "logs" / f"loop-{name}.log", "a") as f:
        f.write(f"{utcnow()} {msg}\n")


def _sleep(cfg: Config, name: str, seconds: int) -> None:
    end = time.time() + seconds
    while time.time() < end and not stopped(cfg, name):
        time.sleep(min(10, max(0.0, end - time.time())))


def run_session(cfg: Config, m: Member, wt: Path, i: int) -> tuple[int, float, Path]:
    prompt = SESSION_PROMPT.format(name=m.name, workdir=cfg.workdir, lane=lane_file(cfg, m), now=utcnow(), i=i)
    argv, extra = runners.build(cfg, m, prompt, mode="work")
    env = {**os.environ, **extra, "CONCLAVE_SESSION": str(i), "CONCLAVE_LOCK_DIR": str(cfg.workdir / "run")}
    log = cfg.workdir / "logs" / f"{m.name}-{time.strftime('%m%dT%H%M', time.gmtime())}-{i}.log"
    start = time.time()
    with open(log, "w") as out:
        try:
            p = subprocess.run(argv, cwd=wt, env=env, stdout=out, stderr=subprocess.STDOUT,
                               stdin=subprocess.DEVNULL, timeout=cfg.loop.session_timeout_min * 60)
            code = p.returncode
        except subprocess.TimeoutExpired:
            code = -1
            out.write(f"\n[conclave] session timed out after {cfg.loop.session_timeout_min} min\n")
        except FileNotFoundError:
            code = 127
            out.write(f"\n[conclave] runner not found: {argv[0]} (is it installed and on PATH?)\n")
    return code, time.time() - start, log


def loop(cfg: Config, name: str, once: bool = False) -> int:
    m = cfg.cardinal(name)
    scaffold(cfg)
    wt = ensure_worktree(cfg, m)
    pid_path(cfg, name).write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    push = cfg.loop.push and has_remote(cfg.root)
    i = read_state(cfg, name).get("sessions", 0)
    _log(cfg, name, f"loop started in {wt} (runner {m.runner}{' / ' + m.model if m.model else ''})")
    try:
        while not stopped(cfg, name):
            if cfg.loop.max_sessions and i >= cfg.loop.max_sessions:
                _log(cfg, name, f"max_sessions {cfg.loop.max_sessions} already reached")
                break
            i += 1
            if push:
                git(wt, "pull", "-q", "--rebase", check=False)
            head_before = git(wt, "rev-parse", "HEAD", check=False)
            _log(cfg, name, f"session {i} starting")
            st = {"sessions": i, "running": True, "started": utcnow(), **{
                k: v for k, v in read_state(cfg, name).items() if k.startswith("last_")}}
            state_path(cfg, name).write_text(json.dumps(st))
            code, dur, log = run_session(cfg, m, wt, i)
            if push:
                git(wt, "pull", "-q", "--rebase", check=False)
                git(wt, "push", "-q", "-u", "origin", "HEAD", check=False)
            _log(cfg, name, f"session {i} exited {code} after {dur:.0f}s ({log.name})")
            state_path(cfg, name).write_text(json.dumps({
                "sessions": i, "running": False, "last_exit": code, "last_duration_s": round(dur),
                "last_end": utcnow(), "last_log": str(log)}))
            if once or (cfg.loop.max_sessions and i >= cfg.loop.max_sessions):
                _log(cfg, name, "session limit reached" if not once else "single session done")
                break
            # A session that ends fast AND fails or commits nothing is usually auth, a usage limit or an
            # outage: back off instead of hammering. A fast session that committed work is just fast.
            progressed = git(wt, "rev-parse", "HEAD", check=False) != head_before
            trouble = dur < cfg.loop.fast_exit_s and (code != 0 or not progressed)
            if trouble:
                _log(cfg, name, f"fast exit without progress: backing off {cfg.loop.backoff_s}s")
            _sleep(cfg, name, cfg.loop.backoff_s if trouble else cfg.loop.pause_between_s)
    finally:
        _log(cfg, name, "loop stopped")
        pid_path(cfg, name).unlink(missing_ok=True)
    return 0


def spawn(cfg: Config, name: str) -> int:
    """Starts `conclave loop <name>` detached. Returns its pid."""
    log = open(cfg.workdir / "logs" / f"loop-{name}.out", "a")
    p = subprocess.Popen([sys.executable, "-m", "conclave", "loop", name], cwd=cfg.root, stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                         env={**os.environ, "CONCLAVE_LOCAL": "1"})
    return p.pid


def kill(cfg: Config, name: str) -> bool:
    pid = alive(cfg, name)
    if not pid:
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        os.kill(pid, signal.SIGTERM)
    return True


def with_lock(cfg: Config, lock: str, cmd: list[str]) -> int:
    """Runs cmd while holding an exclusive lock (e.g. one shared GPU). Hold it only around timing."""
    lock_dir = Path(os.environ.get("CONCLAVE_LOCK_DIR", cfg.workdir / "run"))
    lock_dir.mkdir(parents=True, exist_ok=True)
    with open(lock_dir / f"{lock}.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        return subprocess.run(cmd).returncode
