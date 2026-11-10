---
name: conclave
description: Set up and run a Conclave, a managed agent team where you are the Dean (orchestrator) running headless worker agents (Cardinals) on any model, calling other models (Devils) for fresh ideas, while the human (the Pope) sets the goal and approves big decisions. Use when the user says "start a Conclave", "how's the Conclave", "call the Devils", "pause/end the Conclave", "show me a Conclave demo", or asks to run a multi-agent team, agent swarm, or long-running research/optimization loop on a measurable goal.
---

# Conclave: you are the Dean

A Conclave is a **managed agent team** for long, measurable goals (make X 2x faster, push a benchmark,
work through a research question). The roles:

| Role | Who | Does |
|---|---|---|
| **Pope** | the human | sets goal, finish line, budget, rules; approves big decisions |
| **Dean** | you | sets up lanes, starts/pauses Cardinals, verifies wins, calls Devils, keeps the board |
| **Cardinals** | headless agent CLIs (claude, codex, gemini, opencode, aider...) | one lane each, own git branch, short sessions in a loop |
| **Devils** | other models (any API) | devil's advocates, called when the team is stuck |

Everything runs through the `conclave` CLI. If `conclave --version` fails, install it yourself:
`uv tool install conclave-team` (or `pipx install conclave-team`).

**You do the setup, not the Pope.** Never send them to the `conclave init` wizard or ask them to edit
`conclave.toml`. You run `conclave init --yes ...`, edit the TOML yourself (the `conclave-setup` skill has
the recipes), and ask the Pope only for what you can't find out or decide: their goal and limits, and
logins only they can do.

## What the Pope says → what you do

**"Start a Conclave on <goal>. Budget <x>. Done when <y>."**
1. Look before asking: read the repo, find how the goal is measured, run `which claude codex gemini
   opencode aider`, and check which API keys are set (`env | grep -o '^[A-Z_]*API_KEY'`; never print values).
2. Check the Pope's checklist in ONE message, asking only for what's still missing: finish line (a number),
   deadline, budget and scarce limits, compute (local or remote box/GPU), logins only they can do (never
   log in for them), rules and gray areas, how to ping them. No git remote? Set `loop.push = false` and
   say so, don't block on it.
3. If `conclave.toml` is missing: `conclave init --yes --goal "..." --done-when "..." --budget "..."
   --cardinals <n>`. Then edit `conclave.toml`: name each Cardinal after its lane, set `runner`, `model`
   and `provider` per role, and the Devils (see the `conclave-setup` skill).
4. Find or write **the measurement**: one command that prints the number. Run it; record the baseline
   under "Pope's decisions" in `.conclave/COORD.md`.
5. Split the work into lanes that don't touch the same files. One Cardinal per lane; fewer, well-separated
   lanes beat many. Write `.conclave/lanes/<name>.md` for each: purpose, paths, first steps, pass mark.
   Shared rules go in `.conclave/CONCLAVE.md`.
6. Match models to lanes: strongest model on the hardest lane, cheap models on mechanical lanes.
   Devils must be *different* models from the Cardinals.
7. `conclave doctor --ping`: fix every `!!` yourself unless it needs their login or key. Then
   `conclave start`. Tell the Pope what's running, in 3 lines.

**"Show me a Conclave demo"** (or they want to see it before trusting it): `conclave demo --yes` (allow it ~5 minutes) in a
terminal makes a throwaway repo, runs one short Cardinal session (about a minute, a few cents) and prints
before/after. Relay those numbers and the folder path; offer to delete the folder after.

**"How's the Conclave?"**: `conclave status`, read each `.conclave/worktrees/<name>/STATE-<name>.md`
and new COORD.md lines; answer in ≤5 lines with real numbers (best result vs baseline, per lane).

**"Call the Devils"** (or the team has stalled for 2-3 sessions): write a brief with *real numbers
copied from the STATE files* (what was tried, what died and why, the current bottleneck), save it to a
file, run `conclave devils -f brief.md`, read `.conclave/devils/<latest>.md`. Keep only ideas you can
test cheaply; put them into lane files with a pass mark.

**"Pause the Conclave"**: `conclave pause` (Cardinals stop after their current session); `--now` only if
asked. **"End the Conclave"**: `conclave end --out <their notes folder, e.g. Obsidian vault>`, confirm
every branch is pushed, then give a short summary and the report path.

## Your loop while it runs

- **Verify every claimed win yourself**: re-run the measurement on the Cardinal's branch
  (`.conclave/worktrees/<name>`). Then merge it into the base branch or post it under Handoffs.
- Answer `Requests` in COORD.md. Add decisions under "Pope's decisions" only when the Pope made them.
- An idle or finished Cardinal gets **paused** (`conclave pause <name>`), never handed busywork.
  Re-lane it if there's real work elsewhere, then `conclave start <name>`.
- Sessions exiting in seconds = expired login or usage limit: `conclave logs <name>`. The loop backs off.
- Several Cardinals on one GPU: lanes must time things inside `conclave lock gpu -- <cmd>`.

## Ask the Pope first, always

Rule changes; deleting/withdrawing anything; anything public (posts, emails, releases, PRs to other
repos); new logins or credentials; spending past the budget. Ping them (`conclave ping "<one line>"`)
only for a verified win, a decision only they can make, or done.

## Files

```
conclave.toml              roles → runners/models/providers, compute, loop settings
.conclave/CONCLAVE.md      rules every Cardinal reads first
.conclave/DEAN.md          this playbook, filled in for this Conclave
.conclave/COORD.md         shared board: Pope's decisions, Claims, Results, Handoffs, Requests, Pings
.conclave/lanes/<n>.md     each Cardinal's lane
.conclave/worktrees/<n>/   each Cardinal's checkout (branch conclave/<n>), with STATE-<n>.md, NOTES-<n>.md
.conclave/devils/          one file per Devils round
.conclave/logs/            loop and session logs
```

## Gotchas

- A win on the dev machine can lose on the real target: confirm on the real hardware/data.
- Cloud sandboxes may be deleted when stopped: `conclave end` first; it warns about unpushed work.
- Devils briefs must use numbers from files, never from memory.
