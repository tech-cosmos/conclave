"""A Devils round: every Devil gets the same brief in parallel and argues back, independently.

Devils are deliberately *different* models from the Cardinals, so they don't share their blind spots.
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conclave import runners
from conclave.config import Config, Member
from conclave.providers import chat
from conclave.workspace import board_append, template, utcnow, worktree

DEFAULT_PERSONA = "a sharp, skeptical senior engineer who has seen this kind of problem before"


def context(cfg: Config, max_chars: int = 24000) -> str:
    """The goal plus every Cardinal's STATE file: the real numbers, not memory."""
    parts = [f"GOAL: {cfg.goal}\nDONE WHEN: {cfg.done_when}\nBUDGET: {cfg.budget}"]
    for c in cfg.cardinals:
        for base in (worktree(cfg, c), cfg.root):
            f = base / f"STATE-{c.name}.md"
            if f.exists():
                parts.append(f"--- STATE-{c.name}.md (Cardinal {c.name}: {c.lane.splitlines()[0] if c.lane else ''})\n"
                             + f.read_text()[:6000])
                break
    text = "\n\n".join(parts)
    return text[:max_chars]


def ask_one(cfg: Config, d: Member, brief: str, ctx: str) -> str:
    system = template("devil-system.md").replace("{{persona}}", d.persona or DEFAULT_PERSONA)
    user = f"{ctx}\n\n=== THE BRIEF FROM THE DEAN ===\n{brief}"
    try:
        if d.runner == "api":
            return chat(cfg.provider_for(d), d.model or "", system, user)
        argv, extra = runners.build(cfg, d, f"{system}\n\n{user}", mode="advise")
        p = subprocess.run(argv, cwd=cfg.root, env={**os.environ, **extra}, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=1800)
        out = p.stdout.strip()
        return out if p.returncode == 0 and out else f"(failed, exit {p.returncode})\n{out}\n{p.stderr[-2000:]}"
    except Exception as e:
        return f"(failed: {e})"


def round_(cfg: Config, brief: str, only: list[str] | None = None) -> Path:
    devils = [d for d in cfg.devils if not only or d.name in only]
    if not devils:
        raise SystemExit("no Devils configured: add [[devils]] to conclave.toml")
    ctx = context(cfg)
    with ThreadPoolExecutor(max_workers=len(devils)) as ex:
        answers = list(ex.map(lambda d: ask_one(cfg, d, brief, ctx), devils))
    stamp = utcnow().replace(":", "")
    out = cfg.workdir / "devils" / f"{stamp}.md"
    body = [f"# Devils round {utcnow()}", "", "## Brief", "", brief, ""]
    for d, a in zip(devils, answers):
        on = d.runner + (f" / {d.model}" if d.model else "") + (f" via {d.provider}" if d.provider else "")
        body += [f"## {d.name} ({on})", "", a, ""]
    out.write_text("\n".join(body))
    board_append(cfg, "Results", f"{utcnow()} devils: round of {len(devils)} on \"{brief[:80]}\" -> {out.name}")
    return out
