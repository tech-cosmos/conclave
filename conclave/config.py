"""Loads and validates conclave.toml.

The file maps every role to a *runner* (an agent CLI such as claude or codex, or a plain API call)
and, optionally, a *provider* (where the model lives and which key to use).
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "conclave.toml"
WORKDIR_NAME = ".conclave"

PROVIDER_KINDS = {"openai", "anthropic"}
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,30}$")


class ConfigError(Exception):
    pass


@dataclass
class Provider:
    name: str
    kind: str  # "openai" (any OpenAI-compatible endpoint) or "anthropic" (Messages API)
    base_url: str | None = None
    api_key_env: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def api_key(self) -> str | None:
        return os.environ.get(self.api_key_env) if self.api_key_env else None

    def resolved_base_url(self) -> str:
        if self.base_url:
            return self.base_url.rstrip("/")
        return "https://api.anthropic.com" if self.kind == "anthropic" else "https://api.openai.com/v1"


@dataclass
class Member:
    """A Cardinal, a Devil or the Dean."""

    name: str
    role: str  # "dean" | "cardinal" | "devil"
    runner: str  # claude | codex | gemini | opencode | aider | api | custom
    model: str | None = None
    provider: str | None = None
    lane: str = ""
    paths: list[str] = field(default_factory=list)
    command: list[str] | None = None  # runner = "custom": argv template with {prompt} {model}
    args: list[str] = field(default_factory=list)  # extra CLI args appended to the runner command
    env: dict[str, str] = field(default_factory=dict)
    persona: str = ""  # devils: what kind of contrarian this one is


@dataclass
class Compute:
    backend: str = "local"  # local | remote
    exec_prefix: list[str] = field(default_factory=list)  # remote: e.g. ["ssh", "gpu-box"]
    remote_dir: str = ""  # remote: where the repo is cloned on the box
    locks: list[str] = field(default_factory=list)  # shared scarce resources, e.g. ["gpu"]


@dataclass
class Loop:
    session_timeout_min: int = 90
    pause_between_s: int = 30
    backoff_s: int = 900  # after a session that ends too fast (auth, usage limit, outage)
    fast_exit_s: int = 300
    max_sessions: int = 0  # 0 = until stopped
    push: bool = True


@dataclass
class Pope:
    notify: str = "desktop"  # none | desktop | ntfy:<topic> | command:<shell command with {message}>
    approve: list[str] = field(default_factory=lambda: [
        "rule changes", "deleting or withdrawing anything", "anything public (posts, emails, releases)",
        "new logins or credentials", "spending past the budget",
    ])


@dataclass
class Config:
    root: Path
    name: str
    goal: str
    done_when: str
    budget: str
    rules: list[str]
    base_branch: str
    providers: dict[str, Provider]
    dean: Member
    cardinals: list[Member]
    devils: list[Member]
    compute: Compute
    loop: Loop
    pope: Pope

    @property
    def workdir(self) -> Path:
        return self.root / WORKDIR_NAME

    def cardinal(self, name: str) -> Member:
        for c in self.cardinals:
            if c.name == name:
                return c
        raise ConfigError(f"no cardinal named {name!r} (have: {', '.join(c.name for c in self.cardinals)})")

    def provider_for(self, m: Member) -> Provider | None:
        if not m.provider:
            return None
        if m.provider not in self.providers:
            raise ConfigError(f"{m.role} {m.name!r} uses unknown provider {m.provider!r}")
        return self.providers[m.provider]


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get("CONCLAVE_ROOT")  # set for every Cardinal, whose worktree may hold its own copy
    if env and (Path(env) / CONFIG_NAME).exists():
        return Path(env)
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / CONFIG_NAME).exists():
            return d
    raise ConfigError(f"no {CONFIG_NAME} found here or above. Run `conclave init` first.")


def _member(d: dict, role: str, default_name: str) -> Member:
    known = {"name", "runner", "model", "provider", "lane", "paths", "command", "args", "env", "persona"}
    extra = set(d) - known
    if extra:
        raise ConfigError(f"{role} {d.get('name', default_name)!r}: unknown keys {sorted(extra)}")
    name = d.get("name", default_name)
    if not NAME_RE.match(name):
        raise ConfigError(f"{role} name {name!r}: use lowercase letters, digits and dashes (max 31)")
    runner = d.get("runner", "api" if role == "devil" and d.get("provider") else "claude")
    if runner == "custom" and not d.get("command"):
        raise ConfigError(f"{role} {name!r}: runner = \"custom\" needs command = [...]")
    if runner == "api" and not d.get("provider"):
        raise ConfigError(f"{role} {name!r}: runner = \"api\" needs a provider")
    return Member(
        name=name, role=role, runner=runner, model=d.get("model"), provider=d.get("provider"),
        lane=d.get("lane", ""), paths=list(d.get("paths", [])), command=d.get("command"),
        args=list(d.get("args", [])), env=dict(d.get("env", {})), persona=d.get("persona", ""),
    )


def load(root: Path | None = None) -> Config:
    root = root or find_root()
    path = root / CONFIG_NAME
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from e

    c = raw.get("conclave", {})
    providers = {}
    for pname, p in raw.get("providers", {}).items():
        kind = p.get("kind", "openai")
        if kind not in PROVIDER_KINDS:
            raise ConfigError(f"provider {pname!r}: kind must be one of {sorted(PROVIDER_KINDS)}")
        providers[pname] = Provider(pname, kind, p.get("base_url"), p.get("api_key_env"), dict(p.get("headers", {})))

    dean = _member(raw.get("dean", {}), "dean", "dean")
    cardinals = [_member(d, "cardinal", f"cardinal-{i + 1}") for i, d in enumerate(raw.get("cardinals", []))]
    devils = [_member(d, "devil", f"devil-{i + 1}") for i, d in enumerate(raw.get("devils", []))]

    names = [m.name for m in cardinals + devils]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ConfigError(f"duplicate names: {sorted(dupes)}")

    comp = raw.get("compute", {})
    compute = Compute(
        backend=comp.get("backend", "local"), exec_prefix=list(comp.get("exec_prefix", [])),
        remote_dir=comp.get("remote_dir", ""), locks=list(comp.get("locks", [])),
    )
    if compute.backend not in {"local", "remote"}:
        raise ConfigError("compute.backend must be \"local\" or \"remote\"")
    if compute.backend == "remote" and (not compute.exec_prefix or not compute.remote_dir):
        raise ConfigError("compute.backend = \"remote\" needs exec_prefix (e.g. [\"ssh\", \"box\"]) and remote_dir")

    lp = raw.get("loop", {})
    loop = Loop(**{k: lp[k] for k in Loop.__dataclass_fields__ if k in lp})
    pp = raw.get("pope", {})
    pope = Pope(**{k: pp[k] for k in Pope.__dataclass_fields__ if k in pp})

    cfg = Config(
        root=root, name=c.get("name", root.name), goal=c.get("goal", ""), done_when=c.get("done_when", ""),
        budget=c.get("budget", ""), rules=list(c.get("rules", [])), base_branch=c.get("base_branch", "main"),
        providers=providers, dean=dean, cardinals=cardinals, devils=devils, compute=compute, loop=loop, pope=pope,
    )
    for m in [dean, *cardinals, *devils]:
        cfg.provider_for(m)  # validates provider names
    return cfg
