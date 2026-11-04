# Working on Conclave itself

- Pure Python 3.11+, **standard library only** (no runtime dependencies). Keep it that way.
- Layout: `conclave/cli.py` (commands), `config.py` (conclave.toml), `runners.py` (role → CLI argv),
  `providers.py` (HTTP to model APIs), `loop.py` (a Cardinal's session loop), `workspace.py` (files, worktrees,
  the COORD.md board), `devils.py`, `notify.py`, `templates/` (CONCLAVE.md, DEAN.md, COORD.md, lane.md).
- Skills live in `skills/`; the Claude Code plugin manifests in `.claude-plugin/`. Check with `claude plugin validate .`.
- Tests: `python -m unittest discover tests`. New runners need a test in `test_runner_commands`.
- Adding a runner: one branch in `runners.build()`, the binary in `BINARIES`, a line in `AGENT_RUNNERS`, docs in
  `skills/conclave-setup/SKILL.md`.
