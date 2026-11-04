"""Run: python -m unittest discover tests"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from conclave import config, runners
from conclave.providers import provider_env
from conclave.workspace import board_append, board_section, scaffold

FAKE_AGENT = textwrap.dedent("""\
    #!/bin/bash
    echo "$1" > last_prompt.txt
    echo "s$CONCLAVE_SESSION" >> STATE-$CONCLAVE_NAME.md
    git add -A && git commit -qm "$CONCLAVE_NAME s$CONCLAVE_SESSION: score $CONCLAVE_SESSION"
    "$PYTHON" -m conclave board Results "$CONCLAVE_NAME s$CONCLAVE_SESSION done"
""")


def make_repo(tmp: Path, toml: str) -> Path:
    root = tmp / "repo"
    root.mkdir()
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "commit", "-q", "--allow-empty", "-m", "init"]):
        subprocess.run(cmd, cwd=root, check=True, env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                                       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})
    (root / "conclave.toml").write_text(textwrap.dedent(toml))
    return root


BASE = """
[conclave]
name = "t"
goal = "go faster"
done_when = "fast"

[providers.router]
kind = "openai"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "TEST_ROUTER_KEY"

[providers.glm]
kind = "anthropic"
base_url = "https://example.com/anthropic"
api_key_env = "TEST_GLM_KEY"

[[cardinals]]
name = "alpha"
runner = "claude"
model = "sonnet"

[[cardinals]]
name = "beta"
runner = "claude"
provider = "glm"
model = "glm-4.6"

[[cardinals]]
name = "gamma"
runner = "opencode"
model = "openrouter/qwen/qwen3-coder"

[[devils]]
name = "skeptic"
provider = "router"
model = "deepseek/deepseek-r1"

[[devils]]
name = "codex-devil"
runner = "codex"
"""


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = make_repo(self.tmp, BASE)

    def test_load(self):
        cfg = config.load(self.root)
        self.assertEqual([c.name for c in cfg.cardinals], ["alpha", "beta", "gamma"])
        self.assertEqual(cfg.devils[0].runner, "api")  # provider without runner -> api
        self.assertEqual(cfg.providers["glm"].kind, "anthropic")

    def test_unknown_provider(self):
        (self.root / "conclave.toml").write_text(BASE + '\n[[devils]]\nname = "x"\nprovider = "nope"\nmodel = "m"\n')
        with self.assertRaises(config.ConfigError):
            config.load(self.root)

    def test_bad_name_and_unknown_key(self):
        (self.root / "conclave.toml").write_text(BASE + '\n[[cardinals]]\nname = "Bad Name"\n')
        with self.assertRaises(config.ConfigError):
            config.load(self.root)
        (self.root / "conclave.toml").write_text(BASE + '\n[[cardinals]]\nname = "ok"\nmodle = "typo"\n')
        with self.assertRaises(config.ConfigError):
            config.load(self.root)

    def test_runner_commands(self):
        cfg = config.load(self.root)
        argv, _ = runners.build(cfg, cfg.cardinal("alpha"), "PROMPT")
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertIn("--dangerously-skip-permissions", argv)
        self.assertEqual(argv[-1], "PROMPT")
        argv, _ = runners.build(cfg, cfg.cardinal("gamma"), "PROMPT")
        self.assertEqual(argv, ["opencode", "run", "-m", "openrouter/qwen/qwen3-coder", "PROMPT"])
        argv, _ = runners.build(cfg, cfg.devils[1], "PROMPT", mode="advise")
        self.assertIn("read-only", argv)
        with self.assertRaises(config.ConfigError):
            runners.build(cfg, cfg.devils[0], "PROMPT")  # api runner cannot work as an agent

    def test_provider_env_points_claude_code_at_another_api(self):
        os.environ["TEST_GLM_KEY"] = "k1"
        cfg = config.load(self.root)
        _, env = runners.build(cfg, cfg.cardinal("beta"), "P")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://example.com/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "k1")
        os.environ["TEST_ROUTER_KEY"] = "k2"
        self.assertEqual(provider_env(cfg.providers["router"])["OPENAI_BASE_URL"], "https://openrouter.ai/api/v1")


class BoardTests(unittest.TestCase):
    def test_concurrent_appends_lose_nothing(self):
        tmp = Path(tempfile.mkdtemp())
        cfg = config.load(make_repo(tmp, BASE))
        scaffold(cfg)
        with ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda i: board_append(cfg, "Results", f"line {i}"), range(200)))
        self.assertEqual(len(board_section(cfg, "Results")), 200)
        self.assertTrue(board_section(cfg, "Pope's decisions"))


class LoopTests(unittest.TestCase):
    def test_two_sessions_commit_and_report(self):
        tmp = Path(tempfile.mkdtemp())
        agent = tmp / "agent.sh"
        agent.write_text(FAKE_AGENT)
        agent.chmod(0o755)
        root = make_repo(tmp, f"""
            [conclave]
            name = "loop"
            goal = "g"
            [[cardinals]]
            name = "solo"
            runner = "custom"
            command = ["{agent}", "{{prompt}}"]
            [loop]
            max_sessions = 2
            pause_between_s = 0
            push = false
        """)
        env = {**os.environ, "PYTHON": sys.executable, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", "CONCLAVE_LOCAL": "1",
               "PYTHONPATH": str(Path(__file__).resolve().parents[1])}  # works from a plain checkout
        r = subprocess.run([sys.executable, "-m", "conclave", "loop", "solo"], cwd=root, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        wt = root / ".conclave/worktrees/solo"
        log = subprocess.run(["git", "log", "--format=%s"], cwd=wt, capture_output=True, text=True).stdout
        self.assertIn("solo s2: score 2", log)
        self.assertIn('Cardinal "solo"', (wt / "last_prompt.txt").read_text())
        st = json.loads((root / ".conclave/run/solo.json").read_text())
        self.assertEqual((st["sessions"], st["last_exit"]), (2, 0))
        cfg = config.load(root)
        self.assertEqual(len(board_section(cfg, "Results")), 2)


if __name__ == "__main__":
    unittest.main()
