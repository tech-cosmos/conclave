"""conclave: run a managed agent team from the terminal."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from conclave import __version__, devils, loop, notify, runners
from conclave.config import CONFIG_NAME, ConfigError, Config, find_root, load
from conclave.providers import ProviderError, chat
from conclave.workspace import (board_append, board_section, git, has_remote, is_git_repo, scaffold, unpushed,
                                utcnow, worktree)

C = {"b": "\033[1m", "d": "\033[2m", "r": "\033[31m", "g": "\033[32m", "y": "\033[33m", "x": "\033[0m"}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    C = {k: "" for k in C}


def say(msg: str = "") -> None:
    for k, v in C.items():  # color tokens only; never str.format() text that may hold JSON braces
        msg = msg.replace("{" + k + "}", v)
    print(msg)


# ---------------------------------------------------------------- init

KNOWN_KEYS = [  # env var -> (provider name, kind, base_url, a sensible Devil model)
    ("OPENROUTER_API_KEY", "openrouter", "openai", "https://openrouter.ai/api/v1", "deepseek/deepseek-r1"),
    ("ANTHROPIC_API_KEY", "anthropic", "anthropic", None, "claude-opus-5-5"),
    ("OPENAI_API_KEY", "openai", "openai", os.environ.get("OPENAI_BASE_URL"), os.environ.get("OPENAI_MODEL", "gpt-5")),
    ("GEMINI_API_KEY", "gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.1-pro-preview"),
    ("DEEPSEEK_API_KEY", "deepseek", "openai", "https://api.deepseek.com", "deepseek-reasoner"),
    ("GROQ_API_KEY", "groq", "openai", "https://api.groq.com/openai/v1", "moonshotai/kimi-k2-instruct"),
]


def ask(q: str, default: str = "", yes: bool = False) -> str:
    if yes:
        return default
    d = f" [{default}]" if default else ""
    try:
        a = input(f"{q}{d}: ").strip()
    except EOFError:
        a = ""
    return a or default


def q(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def cmd_init(a: argparse.Namespace) -> int:
    root = Path.cwd()
    if (root / CONFIG_NAME).exists() and not a.force:
        say(f"{{y}}{CONFIG_NAME} already exists.{{x}} Edit it, or re-run with --force.")
        return 1
    if not is_git_repo(root):
        say("{y}Not a git repo.{x} Cardinals work on their own branches, so Conclave needs one.")
        if a.yes or ask("Run `git init` here? (y/n)", "y").lower().startswith("y"):
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "Start a Conclave"], cwd=root, check=True)
        else:
            return 1
    if not git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False):
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "Start a Conclave"], cwd=root, check=True)
    base = git(root, "branch", "--show-current", check=False) or "main"

    y = a.yes
    say("\n{b}Start a Conclave.{x} You are the Pope: set the goal, the finish line and the budget.\n")
    name = ask("Name", a.name or root.name.lower().replace(" ", "-"), y)
    goal = ask("Goal", a.goal or "Make <something> measurably better", y)
    done = ask("Done when", a.done_when or "<a number you can measure>", y)
    budget = ask("Budget (money, hours, scarce runs)", a.budget or "2 days, $20 of API calls", y)

    installed = [r for r in runners.BINARIES if shutil.which(runners.BINARIES[r])]
    keys = [k for k in KNOWN_KEYS if os.environ.get(k[0])]
    say(f"\n{{d}}Agent CLIs found: {', '.join(installed) or 'none'}. "
        f"API keys found: {', '.join(k[0] for k in keys) or 'none'}.{{x}}")
    default_runner = installed[0] if installed else "claude"

    n = int(ask("How many Cardinals (workers)", str(a.cardinals or 2), y) or 2)
    cards = []
    for i in range(n):
        cname = ask(f"  Cardinal {i + 1} name", f"cardinal-{i + 1}", y)
        lane = ask(f"  {cname}'s lane (one line; the Dean refines it)", getattr(a, "lane", None) or "", y)
        runner = ask(f"  {cname} runs on ({'/'.join(runners.AGENT_RUNNERS)})", default_runner, y)
        cheap = getattr(a, "cheap_models", {}).get(runner, "")
        model = ask(f"  {cname} model (blank = the CLI's default)", cheap, y)
        cards.append((cname, lane, runner, model))

    devil_rows = []
    for env, pname, kind, url, model in keys[:2]:
        devil_rows.append((f"{pname}-devil", "api", pname, model))
    for r in ("codex", "gemini"):
        if r in installed and len(devil_rows) < 3 and r != default_runner:
            devil_rows.append((f"{r}-devil", r, None, None))

    lines = [
        "# Conclave: a managed agent team. Docs: https://github.com/tech-cosmos/conclave",
        "# Roles: you are the Pope; the Dean orchestrates; Cardinals work; Devils argue back.",
        "",
        "[conclave]",
        f"name = {q(name)}",
        f"goal = {q(goal)}",
        f"done_when = {q(done)}",
        f"budget = {q(budget)}",
        f"base_branch = {q(base)}",
        "rules = [",
        '  "Test before building: every idea gets a cheap measurement and a pass mark first.",',
        '  "Real improvements only: no gaming the metric, no editing the benchmark.",',
        "]",
        "",
        "[pope]",
        'notify = "desktop"   # none | desktop | ntfy:<topic> | command:<shell cmd using {message}>',
        "",
        "# ---- Providers: where models live. kind = openai (any OpenAI-compatible URL) or anthropic.",
    ]
    for env, pname, kind, url, _ in keys or [KNOWN_KEYS[0]]:
        lines += [f"[providers.{pname}]", f"kind = {q(kind)}", f"api_key_env = {q(env)}"]
        if url:
            lines.append(f"base_url = {q(url)}")
        lines.append("")
    lines += [
        "# [providers.local]                 # e.g. Ollama / LM Studio / vLLM",
        '# kind = "openai"',
        '# base_url = "http://localhost:11434/v1"',
        "",
        "# ---- The Dean: the orchestrator you talk to (`conclave dean` opens it).",
        "[dean]",
        f"runner = {q(default_runner)}",
        "",
        "# ---- Cardinals: one lane each, own branch (conclave/<name>), headless sessions in a loop.",
        "# runner: claude | codex | gemini | opencode | aider | custom.  provider = \"...\" points it at another API.",
    ]
    for cname, lane, runner, model in cards:
        lines += ["[[cardinals]]", f"name = {q(cname)}", f"lane = {q(lane)}", f"runner = {q(runner)}"]
        if model:
            lines.append(f"model = {q(model)}")
        lines.append("")
    lines += ["# ---- Devils: different models, called for fresh ideas when stuck (`conclave devils \"...\"`)."]
    for dname, runner, prov, model in devil_rows:
        lines += ["[[devils]]", f"name = {q(dname)}", f"runner = {q(runner)}"]
        if prov:
            lines.append(f"provider = {q(prov)}")
        if model:
            lines.append(f"model = {q(model)}")
        lines.append("")
    if not devil_rows:
        lines += ["# [[devils]]", '# name = "skeptic"', '# runner = "api"', '# provider = "openrouter"',
                  '# model = "deepseek/deepseek-r1"', ""]
    lines += [
        "[compute]",
        'backend = "local"     # or "remote" with exec_prefix = ["ssh", "gpu-box"] and remote_dir = "~/repo"',
        'locks = []            # shared scarce things, e.g. ["gpu"]; use: conclave lock gpu -- python bench.py',
        "",
        "[loop]",
        "session_timeout_min = 90",
        "pause_between_s = 30",
        "backoff_s = 900       # after a session that dies fast (auth, usage limit, outage)",
        "max_sessions = 0      # 0 = until paused",
        "push = true           # push each Cardinal's branch after every session (if the repo has a remote)",
    ]
    if getattr(a, "demo", False):  # one short session, nothing pushed
        lines = [{"session_timeout_min = 90": "session_timeout_min = 15",
                  "max_sessions = 0      # 0 = until paused": "max_sessions = 1      # demo: one session",
                  }.get(ln, ln) for ln in lines]
        lines = [ln.replace("push = true ", "push = false") if ln.startswith("push = true") else ln for ln in lines]
    (root / CONFIG_NAME).write_text("\n".join(lines) + "\n")
    cfg = load(root)
    scaffold(cfg)
    say(f"\n{{g}}Wrote {CONFIG_NAME} and .conclave/{{x}} (CONCLAVE.md, DEAN.md, COORD.md, lanes/).")
    if getattr(a, "demo", False):
        return 0
    say("Next: {b}conclave doctor{x}, then let the Dean refine the lanes and run {b}conclave start{x}.")
    say("Or open the Dean: {b}conclave dean{x}")
    return 0


# ---------------------------------------------------------------- demo

DEMO_SLOW = '''import time
def count_primes(n):
    return sum(1 for k in range(2, n) if all(k % d for d in range(2, k)))
t = time.perf_counter(); r = count_primes(6000); print(f"primes={r} seconds={time.perf_counter()-t:.3f}")
'''
CHEAP_MODELS = {"claude": "haiku", "codex": "gpt-5-mini", "gemini": "gemini-2.5-flash"}


def _run_slow(path: Path) -> str:
    r = subprocess.run([sys.executable, "slow.py"], cwd=path, capture_output=True, text=True, timeout=120)
    return (r.stdout or r.stderr).strip()


def cmd_demo(a: argparse.Namespace) -> int:
    """A throwaway repo with one slow script, the real wizard pre-filled, one short Cardinal session, a verdict."""
    import tempfile
    import time
    root = Path(a.dir).expanduser() if a.dir else Path(tempfile.mkdtemp(prefix="conclave-demo-"))
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        say(f"{{r}}{root} is not empty.{{x}} Pick an empty folder, or leave it out for a temp one.")
        return 1
    env = {**os.environ, "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "conclave-demo"),
           "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "demo@conclave.local")}
    env.setdefault("GIT_COMMITTER_NAME", env["GIT_AUTHOR_NAME"])
    env.setdefault("GIT_COMMITTER_EMAIL", env["GIT_AUTHOR_EMAIL"])
    (root / "slow.py").write_text(DEMO_SLOW)
    for c in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "A slow script"]):
        subprocess.run(c, cwd=root, check=True, env=env)
    baseline = _run_slow(root)
    say(f"\n{{b}}Conclave demo{{x}} in {{b}}{root}{{x}}")
    say(f"The repo has one slow script. Right now: {{y}}{baseline}{{x}}")
    say("{d}This is the real `conclave init` wizard with demo answers filled in. Press Enter to accept, or type your own.{x}")

    os.chdir(root)
    a.force, a.name, a.cardinals = False, "demo", 1
    a.goal = "Make slow.py at least 20x faster with exactly the same output"
    a.done_when = "slow.py prints the same primes= line in under 1/20 of the baseline time"
    a.budget = "one short session, well under $1"
    a.lane = "Speed up count_primes in slow.py"
    a.cheap_models, a.demo = CHEAP_MODELS, True
    if cmd_init(a):
        return 1
    cfg = load(root)

    say("\n{b}Checking the setup{x}")
    if cmd_doctor(cfg, argparse.Namespace(ping=False)) and not a.yes:
        if not ask("Some checks failed. Start anyway? (y/n)", "n").lower().startswith("y"):
            say(f"Fix them in {root / CONFIG_NAME}, then: cd {root} && conclave start")
            return 1
    c = cfg.cardinals[0]
    if not a.yes and not ask(f"\nStart Cardinal {c.name} for one session ({c.runner}"
                             f"{' / ' + c.model if c.model else ''})? (y/n)", "y").lower().startswith("y"):
        say(f"Whenever you like: cd {root} && conclave start")
        return 0
    cmd_start(cfg, argparse.Namespace(names=[]))
    say("{d}Working. Usually 1-3 minutes. Ctrl-C stops watching; the Cardinal keeps going "
        f"(then: cd {root} && conclave status).{{x}}")
    t0, seen = time.time(), set()
    try:
        time.sleep(3)
        while loop.alive(cfg, c.name):
            for ln in board_section(cfg, "Claims") + board_section(cfg, "Results") + board_section(cfg, "Pings"):
                if ln not in seen and "dean: started" not in ln:
                    seen.add(ln)
                    say(f"\r  {{g}}board{{x}} {ln.lstrip('- ')}")
            print(f"\r  {int(time.time() - t0)}s ", end="", flush=True)
            time.sleep(2)
    except KeyboardInterrupt:
        say(f"\nStopped watching. The Cardinal is still working: cd {root} && conclave status")
        return 0

    wt = worktree(cfg, c)
    st = loop.read_state(cfg, c.name)
    say(f"\r\n{{b}}Session finished{{x}} in {int(time.time() - t0)}s (exit {st.get('last_exit')})")
    commits = git(wt, "log", "--format=%h %s", f"{cfg.base_branch}..HEAD", check=False).splitlines()
    say("\n".join(f"  commit {ln}" for ln in commits) or "  {r}no commits{x}")
    after = _run_slow(wt) if wt.exists() else "?"
    say(f"  before  {baseline}\n  after   {after}")
    same = baseline.split()[0] == after.split()[0]
    say(f"  output {'{g}unchanged{x}' if same else '{r}CHANGED{x} (not a real win)'}")
    say(f"\nLook around: {{b}}cd {root}{{x}} then {{b}}conclave status{{x}}, {{b}}cat .conclave/COORD.md{{x}}, "
        f"{{b}}conclave end{{x}} for the report.")
    say(f"Done with it: {{b}}rm -rf {root}{{x}}")
    return 0


# ---------------------------------------------------------------- doctor

def cmd_doctor(cfg: Config, a: argparse.Namespace) -> int:
    bad = 0

    def line(ok: bool, what: str, hint: str = "") -> None:
        nonlocal bad
        bad += not ok
        say(f"  {'{g}ok{x}' if ok else '{r}!!{x}'}  {what}" + (f"  {{d}}{hint}{{x}}" if hint and not ok else ""))

    say(f"{{b}}Conclave {cfg.name}{{x}}  ({cfg.root})")
    line(is_git_repo(cfg.root), "git repo")
    line(bool(git(cfg.root, "rev-parse", "--verify", "--quiet", cfg.base_branch, check=False)),
         f"base branch {cfg.base_branch}", "set conclave.base_branch")
    line(has_remote(cfg.root) or not cfg.loop.push, "git remote (for pushing Cardinal branches)",
         "add one, or set loop.push = false")
    line(bool(cfg.goal and "<" not in cfg.goal), "goal is set", "edit [conclave] goal")
    line(bool(cfg.cardinals), "at least one Cardinal")
    for m in [cfg.dean, *cfg.cardinals, *cfg.devils]:
        if m.runner == "api":
            line(True, f"{m.role} {m.name}: api call")
        else:
            line(runners.is_installed(m), f"{m.role} {m.name}: runner {m.runner}",
                 f"install `{runners.binary_for(m)}` or pick another runner")
        if m.role == "cardinal" and m.runner == "api":
            line(False, f"cardinal {m.name}: runner api has no tools", "use opencode/aider/claude/codex")
        p = cfg.provider_for(m)
        if p and p.api_key_env:
            line(bool(p.api_key), f"{m.role} {m.name}: ${p.api_key_env} for provider {p.name}", "export it")
    if a.ping:
        say("{b}Pinging providers{x} (one tiny call each)")
        seen = set()
        for m in cfg.devils:
            if m.runner != "api" or (m.provider, m.model) in seen:
                continue
            seen.add((m.provider, m.model))
            try:
                r = chat(cfg.provider_for(m), m.model or "", "Reply with one word.", "Say ok.", max_tokens=20, timeout=60)
                line(True, f"{m.provider} / {m.model}: {r[:40]!r}")
            except ProviderError as e:
                line(False, f"{m.provider} / {m.model}", str(e)[:200])
    say(f"\n{'{g}All good.{x}' if not bad else f'{{r}}{bad} problem(s).{{x}}'}")
    return 1 if bad else 0


# ---------------------------------------------------------------- start / pause / status

def _names(cfg: Config, names: list[str]) -> list[str]:
    for n in names:
        cfg.cardinal(n)
    return names or [c.name for c in cfg.cardinals]


def cmd_start(cfg: Config, a: argparse.Namespace) -> int:
    scaffold(cfg)
    names = _names(cfg, a.names)
    if not a.names:
        (cfg.workdir / "STOP").unlink(missing_ok=True)
    for n in names:
        (cfg.workdir / f"STOP-{n}").unlink(missing_ok=True)
        m = cfg.cardinal(n)
        if m.runner == "api":
            say(f"  {{r}}{n}: runner api cannot be a Cardinal (no tools){{x}}")
            continue
        if not runners.is_installed(m):
            say(f"  {{r}}{n}: `{runners.binary_for(m)}` not found{{x}}")
            continue
        pid = loop.alive(cfg, n)
        if pid:
            say(f"  {n}: already running (pid {pid})")
            continue
        from conclave.workspace import ensure_worktree
        ensure_worktree(cfg, m)
        pid = loop.spawn(cfg, n)
        say(f"  {{g}}{n}{{x}}: started (pid {pid}) in {worktree(cfg, m).relative_to(cfg.root)}")
    board_append(cfg, "Results", f"{utcnow()} dean: started {', '.join(names)}")
    return 0


def cmd_pause(cfg: Config, a: argparse.Namespace) -> int:
    names = _names(cfg, a.names)
    if not a.names:
        (cfg.workdir / "STOP").touch()
    for n in names:
        (cfg.workdir / f"STOP-{n}").touch()
        if a.now and loop.kill(cfg, n):
            say(f"  {n}: killed")
        else:
            say(f"  {n}: will stop after its current session")
    _check_pushed(cfg, names)
    return 0


def _check_pushed(cfg: Config, names: list[str]) -> None:
    for n in names:
        u = unpushed(cfg, cfg.cardinal(n))
        if u:
            say(f"  {{y}}{n}: {u}{{x}} (push before deleting any machine)")


def status_data(cfg: Config) -> dict:
    out = {"name": cfg.name, "goal": cfg.goal, "stopped": (cfg.workdir / "STOP").exists(), "cardinals": []}
    for c in cfg.cardinals:
        wt = worktree(cfg, c)
        st = loop.read_state(cfg, c.name)
        last = git(wt, "log", "-1", "--format=%cr: %s", check=False) if wt.exists() else ""
        state_file = wt / f"STATE-{c.name}.md"
        out["cardinals"].append({
            "name": c.name, "runner": c.runner, "model": c.model, "pid": loop.alive(cfg, c.name),
            "paused": (cfg.workdir / f"STOP-{c.name}").exists(), **st, "last_commit": last,
            "state_head": state_file.read_text().splitlines()[:3] if state_file.exists() else [],
        })
    out["claims"] = board_section(cfg, "Claims")
    out["requests"] = board_section(cfg, "Requests")
    out["pings"] = board_section(cfg, "Pings")[-3:]
    return out


def cmd_status(cfg: Config, a: argparse.Namespace) -> int:
    d = status_data(cfg)
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    say(f"{{b}}Conclave {d['name']}{{x}}: {d['goal']}")
    for c in d["cardinals"]:
        if c["pid"]:
            s = "{g}working{x}" if c.get("running") else "{g}between sessions{x}"
        else:
            s = ("{y}paused{x}" if c["paused"] or d["stopped"] else
                 "{d}loop ended{x}" if c.get("sessions") else "{d}not started{x}")
        model = f" / {c['model']}" if c["model"] else ""
        extra = f"  session {c.get('sessions', 0)}"
        if "last_exit" in c:
            extra += f", last exit {c['last_exit']} after {c['last_duration_s']}s"
        say(f"  {{b}}{c['name']}{{x}} ({c['runner']}{model}): {s}{extra}")
        if c["last_commit"]:
            say(f"    {{d}}last commit {c['last_commit'][:110]}{{x}}")
    for k in ("claims", "requests", "pings"):
        if d[k]:
            say(f"{{b}}{k.title()}{{x}}")
            for ln in d[k][-8:]:
                say(f"  {ln[:140]}")
    return 0


def cmd_end(cfg: Config, a: argparse.Namespace) -> int:
    (cfg.workdir / "STOP").touch()
    say("Stop flag set: every Cardinal stops after its current session.")
    if a.now:
        for c in cfg.cardinals:
            if loop.kill(cfg, c.name):
                say(f"  {c.name}: killed")
    _check_pushed(cfg, [c.name for c in cfg.cardinals])
    out = write_report(cfg, Path(a.out) if a.out else None)
    say(f"Report: {{b}}{out}{{x}}")
    return 0


# ---------------------------------------------------------------- report

def write_report(cfg: Config, dest: Path | None) -> Path:
    lines = [
        "---", f"title: Conclave {cfg.name}", f"created: {utcnow()[:10]}", "tags: [conclave, agent-team]", "---", "",
        f"# Conclave: {cfg.name}", "", f"> **Goal:** {cfg.goal}  ", f"> **Done when:** {cfg.done_when}  ",
        f"> **Budget:** {cfg.budget}", "", "## Cardinals", "",
    ]
    for c in cfg.cardinals:
        wt = worktree(cfg, c)
        st = loop.read_state(cfg, c.name)
        lines += [f"### {c.name} ({c.runner}{' / ' + c.model if c.model else ''})", "",
                  f"Lane: {c.lane or '(see lane file)'}  ", f"Branch: `conclave/{c.name}`, {st.get('sessions', 0)} sessions",
                  ""]
        sf = wt / f"STATE-{c.name}.md"
        if sf.exists():
            lines += ["**Final state:**", "", sf.read_text().strip(), ""]
        if wt.exists():
            log = git(wt, "log", f"{cfg.base_branch}..HEAD", "--format=- %h %s", check=False)
            if log:
                lines += ["**Commits:**", "", log, ""]
    for sec in ("Pope's decisions", "Results", "Handoffs"):
        items = board_section(cfg, sec)
        if items:
            lines += [f"## {sec}", "", *items, ""]
    rounds = sorted((cfg.workdir / "devils").glob("*.md"))
    if rounds:
        lines += ["## Devils rounds", "", *[f"- {r.name}" for r in rounds], ""]
    text = "\n".join(lines)
    out = cfg.workdir / "REPORT.md"
    out.write_text(text)
    if dest:
        dest = dest.expanduser()
        target = dest / f"Conclave - {cfg.name}.md" if dest.is_dir() else dest
        target.write_text(text)
        return target
    return out


def cmd_report(cfg: Config, a: argparse.Namespace) -> int:
    say(str(write_report(cfg, Path(a.out) if a.out else None)))
    return 0


# ---------------------------------------------------------------- devils / ping / lock / logs / dean

def cmd_devils(cfg: Config, a: argparse.Namespace) -> int:
    brief = Path(a.file).read_text() if a.file else " ".join(a.brief)
    if not brief.strip():
        say("{r}Give the Devils a brief:{x} conclave devils \"we're stuck at X; tried A, B; numbers ...\"")
        return 1
    say(f"Calling {len(a.only or cfg.devils)} Devil(s)...")
    out = devils.round_(cfg, brief, a.only)
    say(f"{{g}}Done:{{x}} {out}")
    if a.print:
        print(out.read_text())
    return 0


def cmd_ping(cfg: Config, a: argparse.Namespace) -> int:
    sender = a.sender or os.environ.get("CONCLAVE_NAME", "dean")
    say(notify.ping(cfg, " ".join(a.message), sender))
    return 0


def cmd_board(cfg: Config, a: argparse.Namespace) -> int:
    sender = os.environ.get("CONCLAVE_NAME", "dean")
    board_append(cfg, a.section, f"{utcnow()} {sender}: {' '.join(a.text)}")
    return 0


def cmd_lock(cfg: Config, a: argparse.Namespace) -> int:
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        say("usage: conclave lock <name> -- <command...>")
        return 2
    return loop.with_lock(cfg, a.name, cmd)


def cmd_logs(cfg: Config, a: argparse.Namespace) -> int:
    logs = sorted((cfg.workdir / "logs").glob(f"{a.name}-*.log"), key=lambda p: p.stat().st_mtime)
    if not logs:
        say(f"no session logs for {a.name} yet")
        return 1
    text = logs[-1].read_text(errors="replace").splitlines()
    say(f"{{d}}{logs[-1]}{{x}}")
    print("\n".join(text[-a.n:]))
    return 0


def cmd_loop(cfg: Config, a: argparse.Namespace) -> int:
    return loop.loop(cfg, a.name, once=a.once)


def cmd_dean(cfg: Config, a: argparse.Namespace) -> int:
    scaffold(cfg)
    prompt = (f"You are the Dean of the Conclave \"{cfg.name}\". Read {cfg.workdir}/DEAN.md and follow it. "
              "Start by checking `conclave status` and asking the Pope (me) anything the checklist is missing.")
    r = cfg.dean.runner
    argv = {"claude": ["claude"], "codex": ["codex"], "gemini": ["gemini", "-i"], "opencode": ["opencode", "--prompt"]}.get(r)
    if not argv:
        say(f"Open your agent and paste:\n\n{prompt}")
        return 0
    if cfg.dean.model:
        argv += ["--model" if r != "codex" else "-m", cfg.dean.model]
    from conclave.providers import provider_env
    os.environ.update(provider_env(cfg.provider_for(cfg.dean)))
    os.chdir(cfg.root)
    os.execvp(argv[0], [*argv, prompt])


# ---------------------------------------------------------------- remote forwarding

LOCAL_ONLY = {"init", "dean"}


def forward_remote(cfg: Config, argv: list[str]) -> int:
    inner = f"cd {cfg.compute.remote_dir} && CONCLAVE_LOCAL=1 conclave {shlex.join(argv)}"
    return subprocess.run([*cfg.compute.exec_prefix, "bash", "-lc", shlex.quote(inner)]).returncode


# ---------------------------------------------------------------- main

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="conclave", description="Run a managed agent team: Dean, Cardinals, Devils.")
    p.add_argument("--version", action="version", version=f"conclave {__version__}")
    s = p.add_subparsers(dest="command", required=True)

    i = s.add_parser("init", help="set up conclave.toml and .conclave/ in this repo (wizard)")
    i.add_argument("--yes", "-y", action="store_true", help="accept defaults, no questions")
    i.add_argument("--force", action="store_true")
    for f in ("name", "goal", "done-when", "budget"):
        i.add_argument(f"--{f}")
    i.add_argument("--cardinals", type=int)

    x = s.add_parser("demo", help="try it: a throwaway repo, the wizard pre-filled, one short Cardinal session")
    x.add_argument("dir", nargs="?", help="an empty folder (default: a new temp folder)")
    x.add_argument("--yes", "-y", action="store_true", help="accept every default, no questions")

    d = s.add_parser("doctor", help="check runners, keys and git")
    d.add_argument("--ping", action="store_true", help="make one tiny call to each Devil's provider")

    for name, hlp in (("start", "start (or resume) Cardinal loops"), ("resume", "same as start")):
        x = s.add_parser(name, help=hlp)
        x.add_argument("names", nargs="*")
    x = s.add_parser("pause", help="stop Cardinals after their current session")
    x.add_argument("names", nargs="*")
    x.add_argument("--now", action="store_true", help="kill the running session too")
    x = s.add_parser("status", help="who is doing what")
    x.add_argument("--json", action="store_true")
    x = s.add_parser("end", help="stop everything, check pushes, write the report")
    x.add_argument("--now", action="store_true")
    x.add_argument("--out", help="also write the report here (a file, or a folder such as your Obsidian vault)")
    x = s.add_parser("report", help="write .conclave/REPORT.md")
    x.add_argument("--out")
    x = s.add_parser("devils", help="call the Devils for fresh ideas")
    x.add_argument("brief", nargs="*")
    x.add_argument("--file", "-f")
    x.add_argument("--only", action="append", metavar="NAME", help="only this Devil (repeat for more)")
    x.add_argument("--print", action="store_true")
    x = s.add_parser("ping", help="notify the Pope (wins, decisions, done)")
    x.add_argument("message", nargs="+")
    x.add_argument("--from", dest="sender")
    x = s.add_parser("board", help="append a line to a COORD.md section")
    x.add_argument("section", choices=["Claims", "Results", "Handoffs", "Requests", "Pope's decisions"])
    x.add_argument("text", nargs="+")
    x = s.add_parser("lock", help="run a command holding a shared lock, e.g. conclave lock gpu -- python bench.py")
    x.add_argument("name")
    x.add_argument("cmd", nargs=argparse.REMAINDER)
    x = s.add_parser("logs", help="tail a Cardinal's latest session log")
    x.add_argument("name")
    x.add_argument("-n", type=int, default=40)
    x = s.add_parser("loop", help=argparse.SUPPRESS)
    x.add_argument("name")
    x.add_argument("--once", action="store_true")
    s.add_parser("dean", help="open the Dean (your orchestrator agent) with its playbook")
    return p


HANDLERS = {
    "doctor": cmd_doctor, "start": cmd_start, "resume": cmd_start, "pause": cmd_pause, "status": cmd_status,
    "end": cmd_end, "report": cmd_report, "devils": cmd_devils, "ping": cmd_ping, "board": cmd_board,
    "lock": cmd_lock, "logs": cmd_logs, "loop": cmd_loop, "dean": cmd_dean,
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    a = parser().parse_args(argv)
    try:
        if a.command == "init":
            return cmd_init(a)
        if a.command == "demo":
            return cmd_demo(a)
        cfg = load(find_root())
        if cfg.compute.backend == "remote" and a.command not in LOCAL_ONLY and not os.environ.get("CONCLAVE_LOCAL"):
            return forward_remote(cfg, argv)
        return HANDLERS[a.command](cfg, a) or 0
    except ConfigError as e:
        say(f"{{r}}config:{{x}} {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
