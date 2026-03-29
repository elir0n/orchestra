# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install all dependencies (editable mode, required for imports to resolve)
pip install -e .

# Run the agent (dry-run prints results to stdout, no email sent)
python main.py referral-finder --company "Stripe" --your-name "Name" --your-background "..." --dry-run

# Run with email notification
python main.py referral-finder --company "Stripe" --your-name "Name" --your-background "..."

# List all registered agents
python main.py --help
```

## Architecture

Orchestra is a **multi-agent orchestrator**. The `shared/` package provides the platform; `agents/` contains isolated agent implementations.

### Agent discovery
`shared/orchestrator.py` auto-discovers agents at startup by walking the `agents/` package, importing every `agents.<name>.agent` module, and collecting all `BaseAgent` subclasses. The agent's `name` class variable becomes the CLI subcommand. No registration needed — adding a folder to `agents/` is sufficient.

### Adding a new agent
1. Create `agents/<kebab-name>/agent.py` with a class inheriting `BaseAgent`
2. Set `name = "kebab-name"` and `description = "..."` as class variables
3. Implement `build_arg_parser`, `config_from_args`, and `run`
4. Create `agents/<kebab-name>/config.py` with a dataclass for the agent's settings

Agent-specific env vars are auto-injected by the Orchestrator: prefix them with `AGENTNAME_` (e.g. `REFERRAL_FINDER_GITHUB_TOKEN` → `config.github_token`).

### Referral Finder pipeline
`agents/referral_finder/agent.py` → runs these steps concurrently per candidate (ThreadPoolExecutor):
1. `search/linkedin_search.py` — Tavily query `site:linkedin.com/in "{company}" "{role}"` → `List[Person]`
2. `email_finder/pipeline.py` — 4-tier free email lookup (see below)
3. `generator.py` — single `messages.create` call to Claude (haiku by default)
4. `notifier.py` — one summary email to operator via SMTP

### Email finder tiers (in order, stops at first confident hit)
1. **GitHub API** (`email_finder/github.py`) — search by name+company, extract profile email or commit email; requires `REFERRAL_FINDER_GITHUB_TOKEN`
2. **Pattern cache** (`email_finder/pattern_cache.py`) — persisted in `email_patterns.json` next to the module; keys: domain → pattern, company → domain
3. **Hunter.io domain search** (`email_finder/domain_lookup.py`) — free `/v2/domain-search` endpoint returns email format only (does NOT count toward the 25/month per-person cap)
4. **SMTP RCPT TO** (`email_finder/smtp_verify.py`) — generates permutations, verifies via SMTP handshake without sending mail; auto-skips Microsoft 365 / Mimecast (catch-all servers)

### Configuration
- Global secrets (`ANTHROPIC_API_KEY`, SMTP credentials, `NOTIFY_EMAIL`) → `shared/config.py` → `OrchestraConfig`
- Per-agent secrets use the `AGENTNAME_` prefix and are injected into the agent's config dataclass by `Orchestrator._inject_secrets`
- Copy `.env.example` → `.env` before running
