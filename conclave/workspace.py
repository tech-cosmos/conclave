"""The shared files of a Conclave and each Cardinal's git worktree.

.conclave/
  CONCLAVE.md          rules every Cardinal reads first (generated from conclave.toml, then yours to edit)
  DEAN.md              the Dean's playbook
  COORD.md             the shared board: Pope's decisions, claims, results, handoffs, requests
  lanes/<name>.md      one brief per Cardinal
  worktrees/<name>/    each Cardinal's checkout, on branch conclave/<name>
  devils/              one file per Devils round
  logs/                session logs
  run/                 pid files
  STOP, STOP-<name>    stop flags (a running session finishes its cycle, then the loop exits)
"""

from __future__ import annotations

import fcntl
import os
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from conclave.config import Config, Member

LANE_PLACEHOLDER = "(The Dean writes this lane.)"
BOARD_SECTIONS = ["Pope's decisions", "Claims", "Results", "Handoffs", "Requests", "Pings"]


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def template(name: str) -> str:
    return resources.files("conclave.templates").joinpath(name).read_text()


def render(tpl: str, **kw: str) -> str:
    text = template(tpl)
    for k, v in kw.items():
        text = text.replace("{{" + k + "}}", v)
    return text


def git(cwd: Path, *args: str, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def is_git_repo(path: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--git-dir"], cwd=path, capture_output=True).returncode == 0


def has_remote(path: Path) -> bool:
    return bool(git(path, "remote", check=False))


def branch(m: Member) -> str:
    return f"conclave/{m.name}"


def worktree(cfg: Config, m: Member) -> Path:
    return cfg.workdir / "worktrees" / m.name


def lane_file(cfg: Config, m: Member) -> Path:
    return cfg.workdir / "lanes" / f"{m.name}.md"


def bullet(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none yet: the Pope adds them here)"


def cardinal_table(cfg: Config) -> str:
    rows = ["| Cardinal | Runs on | Lane |", "|---|---|---|"]
    for c in cfg.cardinals:
        on = c.runner + (f" / {c.model}" if c.model else "") + (f" via {c.provider}" if c.provider else "")
        rows.append(f"| {c.name} | {on} | {c.lane.splitlines()[0] if c.lane else '(see lane file)'} |")
    return "\n".join(rows)


def scaffold(cfg: Config, overwrite: bool = False) -> list[Path]:
    """Writes the shared files that don't exist yet. Returns what it wrote."""
    w = cfg.workdir
    for sub in ["lanes", "worktrees", "devils", "logs", "run"]:
        (w / sub).mkdir(parents=True, exist_ok=True)
    common = dict(
        name=cfg.name, goal=cfg.goal or "(set goal in conclave.toml)", done_when=cfg.done_when or "(not set)",
        budget=cfg.budget or "(not set)", rules=bullet(cfg.rules), approve=bullet(cfg.pope.approve),
        cardinals=cardinal_table(cfg), root=str(cfg.root), workdir=str(w), base_branch=cfg.base_branch,
        locks=", ".join(cfg.compute.locks) or "(none)", date=utcnow(),
    )
    wrote = []

    def put(path: Path, text: str) -> None:
        if overwrite or not path.exists():
            path.write_text(text)
            wrote.append(path)

    put(w / "CONCLAVE.md", render("CONCLAVE.md", **common))
    put(w / "DEAN.md", render("DEAN.md", **common))
    put(w / "COORD.md", render("COORD.md", **common))
    for c in cfg.cardinals:
        lf = lane_file(cfg, c)
        if c.lane and lf.exists() and LANE_PLACEHOLDER in lf.read_text():
            lf.unlink()  # lane was written into conclave.toml after init: refresh the untouched placeholder
        put(lf, render(
            "lane.md", **common, cardinal=c.name, lane=c.lane or LANE_PLACEHOLDER,
            paths=bullet(c.paths) if c.paths else "- anywhere in the repo, but stay out of other lanes' paths",
            branch=branch(c),
        ))
    gi = w / ".gitignore"
    put(gi, "*\n")
    return wrote


def ensure_worktree(cfg: Config, m: Member) -> Path:
    wt = worktree(cfg, m)
    if (wt / ".git").exists():
        return wt
    br = branch(m)
    exists = subprocess.run(["git", "rev-parse", "--verify", "--quiet", br], cwd=cfg.root,
                            capture_output=True).returncode == 0
    if exists:
        git(cfg.root, "worktree", "add", str(wt), br)
    else:
        git(cfg.root, "worktree", "add", "-b", br, str(wt), cfg.base_branch)
    return wt


def unpushed(cfg: Config, m: Member) -> str | None:
    """None if pushed (or there is no remote); otherwise a short description."""
    wt = worktree(cfg, m)
    if not wt.exists() or not has_remote(cfg.root):
        return None
    dirty = git(wt, "status", "--porcelain", check=False)
    upstream = git(wt, "rev-parse", "--abbrev-ref", "@{u}", check=False)
    ahead = git(wt, "rev-list", "--count", "@{u}..HEAD", check=False) if upstream else "all"
    problems = []
    if dirty:
        problems.append(f"{len(dirty.splitlines())} uncommitted file(s)")
    if not upstream:
        problems.append("branch never pushed")
    elif ahead not in ("", "0"):
        problems.append(f"{ahead} commit(s) not pushed")
    return ", ".join(problems) or None


# ---------- the shared board (COORD.md) ----------

def board_path(cfg: Config) -> Path:
    return cfg.workdir / "COORD.md"


@contextmanager
def _board_lock(cfg: Config):
    cfg.workdir.mkdir(parents=True, exist_ok=True)
    with open(cfg.workdir / ".coord.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def board_append(cfg: Config, section: str, line: str) -> None:
    """Appends one line under '## <section>' (creating the section if missing). Safe across processes."""
    with _board_lock(cfg):
        _board_append(cfg, section, line)


def _board_append(cfg: Config, section: str, line: str) -> None:
    p = board_path(cfg)
    text = p.read_text() if p.exists() else ""
    if not text.strip():
        text = render("COORD.md", name=cfg.name, date=utcnow(), goal=cfg.goal, done_when=cfg.done_when,
                      budget=cfg.budget)
    lines = text.splitlines()
    header = f"## {section}"
    try:
        i = lines.index(header)
    except ValueError:
        lines += ["", header, ""]
        i = len(lines) - 2
    j = i + 1
    while j < len(lines) and not lines[j].startswith("## "):
        j += 1
    while j > i + 1 and not lines[j - 1].strip():
        j -= 1
    lines.insert(j, f"- {line}")
    tmp = p.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, p)  # atomic: readers never see a half-written board


def board_section(cfg: Config, section: str) -> list[str]:
    p = board_path(cfg)
    if not p.exists():
        return []
    out, inside = [], False
    for ln in p.read_text().splitlines():
        if ln.startswith("## "):
            inside = ln[3:].strip() == section
            continue
        if inside and ln.strip():
            out.append(ln)
    return out
