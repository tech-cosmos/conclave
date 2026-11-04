"""Turns a role (Cardinal, Devil, Dean) into a command line for the agent CLI it runs on.

mode = "work"    full autonomy inside its own worktree (Cardinals)
mode = "advise"  read-only, answer on stdout (Devils that run on an agent CLI)
"""

from __future__ import annotations

import os
import shutil

from conclave.config import Config, ConfigError, Member
from conclave.providers import provider_env

AGENT_RUNNERS = {
    "claude": "Claude Code (Anthropic, or any Anthropic-compatible provider)",
    "codex": "OpenAI Codex CLI (OpenAI, or any OpenAI-compatible provider)",
    "gemini": "Google Gemini CLI",
    "opencode": "opencode (75+ providers, model = \"provider/model\")",
    "aider": "aider (any litellm model)",
    "custom": "your own command, with {prompt} and {model} placeholders",
}
ALL_RUNNERS = {**AGENT_RUNNERS, "api": "a plain chat call to a provider (Devils only: no tools)"}

BINARIES = {"claude": "claude", "codex": "codex", "gemini": "gemini", "opencode": "opencode", "aider": "aider"}


def binary_for(m: Member) -> str | None:
    if m.runner == "custom":
        return m.command[0] if m.command else None
    return BINARIES.get(m.runner)


def is_installed(m: Member) -> bool:
    b = binary_for(m)
    return b is None or shutil.which(b) is not None


def build(cfg: Config, m: Member, prompt: str, mode: str = "work") -> tuple[list[str], dict[str, str]]:
    """Returns (argv, extra_env)."""
    if m.runner == "api":
        raise ConfigError(
            f"{m.role} {m.name!r}: runner \"api\" has no tools, so it can only be a Devil. "
            "For an API model as a Cardinal use runner = \"opencode\" or \"aider\", or claude/codex with a provider."
        )
    model = m.model
    work = mode == "work"
    r = m.runner

    if r == "claude":
        argv = ["claude", "-p"]
        if work:
            argv += ["--dangerously-skip-permissions", "--output-format", "stream-json", "--verbose"]
        else:
            argv += ["--allowedTools", "Read,Grep,Glob,WebSearch,WebFetch", "--output-format", "text"]
        if model:
            argv += ["--model", model]
    elif r == "codex":
        argv = ["codex", "exec", "--skip-git-repo-check"]
        argv += ["--dangerously-bypass-approvals-and-sandbox"] if work else ["--sandbox", "read-only"]
        if model:
            argv += ["-m", model]
    elif r == "gemini":
        argv = ["gemini"]
        if work:
            argv += ["--yolo"]
        if model:
            argv += ["-m", model]
        argv += ["-p"]
    elif r == "opencode":
        argv = ["opencode", "run"]
        if model:
            argv += ["-m", model]
    elif r == "aider":
        argv = ["aider", "--yes-always", "--no-check-update"]
        if model:
            argv += ["--model", model]
        argv += ["--message"]
    elif r == "custom":
        sub = {"prompt": prompt, "model": model or "", "name": m.name, "mode": mode}
        argv = [a.format(**sub) for a in (m.command or [])] + list(m.args)
        return argv, _env(cfg, m)
    else:
        raise ConfigError(f"{m.role} {m.name!r}: unknown runner {r!r} (one of {sorted(ALL_RUNNERS)})")

    argv += list(m.args)
    argv.append(prompt)
    return argv, _env(cfg, m)


def _env(cfg: Config, m: Member) -> dict[str, str]:
    env = provider_env(cfg.provider_for(m))
    for k, v in m.env.items():
        env[k] = os.path.expandvars(v)
    env["CONCLAVE_ROLE"] = m.role
    env["CONCLAVE_NAME"] = m.name
    env["CONCLAVE_ROOT"] = str(cfg.root)
    return env
