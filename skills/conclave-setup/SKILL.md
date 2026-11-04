---
name: conclave-setup
description: Install Conclave and wire its roles to models - which agent CLI and model each Cardinal (worker) runs on, which APIs the Devils (devil's advocates) call, and how to connect providers like Anthropic, OpenAI, OpenRouter, Gemini, DeepSeek, Kimi, GLM, Groq or a local Ollama. Use when the user wants to set up, configure or troubleshoot conclave.toml, connect API keys, choose models per role, or run Cardinals on a remote GPU box.
---

# Setting up a Conclave

## 1. Install

```bash
uv tool install git+https://github.com/tech-cosmos/conclave   # or: pipx install git+https://...
conclave --version
```
Needs Python 3.11+ and git, plus at least one agent CLI for the Cardinals:
`claude` (Claude Code), `codex`, `gemini`, `opencode` or `aider`. Each must be logged in **by the user**.

## 2. Create the config

In the target repo: `conclave init` (interactive), or non-interactive:
`conclave init --yes --goal "..." --done-when "..." --budget "..." --cardinals 3`.
It detects installed CLIs and API keys and writes `conclave.toml` plus `.conclave/`.

## 3. Wire roles to models: the two ideas

- **runner** = the program that runs the role. Cardinals need tools, so they use an agent CLI:
  `claude | codex | gemini | opencode | aider | custom`. Devils can be `api` (a plain chat call, no tools)
  or any agent CLI (run read-only).
- **provider** = where the model lives, and which env var has the key. `kind = "openai"` covers any
  OpenAI-compatible URL; `kind = "anthropic"` covers Anthropic and Anthropic-compatible gateways.
  Setting `provider` on a Cardinal points its CLI at that API (it exports `ANTHROPIC_BASE_URL` /
  `OPENAI_BASE_URL` and the key), so **Claude Code can drive GLM, Kimi or DeepSeek**, and **Codex can drive
  any OpenAI-compatible model**.

### Recipes (copy into conclave.toml)

```toml
[providers.anthropic]
kind = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

[providers.openrouter]                       # 300+ models behind one key
kind = "openai"
base_url = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

[providers.gemini]
kind = "openai"
base_url = "https://generativelanguage.googleapis.com/v1beta/openai"
api_key_env = "GEMINI_API_KEY"

[providers.deepseek]
kind = "openai"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[providers.kimi]                              # Anthropic-compatible: drive it with Claude Code
kind = "anthropic"
base_url = "https://api.moonshot.ai/anthropic"
api_key_env = "MOONSHOT_API_KEY"

[providers.local]                             # Ollama / LM Studio / vLLM: no key
kind = "openai"
base_url = "http://localhost:11434/v1"
```

```toml
[[cardinals]]                 # Claude Code with your Claude login (no provider = the CLI's own auth)
name = "core"
lane = "Make the hot loop in src/engine faster"
runner = "claude"
model = "opus"
paths = ["src/engine/"]

[[cardinals]]                 # Claude Code driving Kimi through its Anthropic-compatible API
name = "io"
runner = "claude"
provider = "kimi"
model = "kimi-k2-thinking"

[[cardinals]]                 # Codex with its own ChatGPT login
name = "tests"
runner = "codex"
model = "gpt-5-codex"

[[cardinals]]                 # opencode with any OpenRouter model
name = "docs"
runner = "opencode"
model = "openrouter/qwen/qwen3-coder"

[[cardinals]]                 # anything else
name = "mine"
runner = "custom"
command = ["my-agent", "--model", "{model}", "--task", "{prompt}"]

[[devils]]                    # a plain API call: cheapest way to get a different brain
name = "skeptic"
provider = "openrouter"
model = "deepseek/deepseek-r1"
persona = "a performance engineer who distrusts micro-benchmarks"

[[devils]]                    # an agent CLI as a Devil: can read the repo (read-only)
name = "codex-devil"
runner = "codex"
```

Model names are whatever that runner/provider accepts. When unsure, leave `model` out to use the CLI's default.

## 4. Compute

Local (default): each Cardinal gets a git worktree in `.conclave/worktrees/<name>`.
Remote GPU box: clone the repo there, install conclave there, log the agent CLIs in there, then locally:
```toml
[compute]
backend = "remote"
exec_prefix = ["ssh", "gpu-box"]   # anything that runs a command on the box
remote_dir = "~/myrepo"
locks = ["gpu"]                    # Cardinals time things inside: conclave lock gpu -- python bench.py
```
Every `conclave` command except `init` and `dean` is then forwarded to the box. Use `notify = "ntfy:<topic>"`
under `[pope]` so pings reach the phone from the box.

## 5. Check it

`conclave doctor --ping` must show no `!!`. Typical fixes: export the missing key; install or log in to
the CLI; add a git remote (or `loop.push = false`); give the Conclave a real goal.
Smoke test: set `[loop] max_sessions = 1`, `conclave start`, watch `conclave status` and
`conclave logs <name>`, then set it back to 0.
