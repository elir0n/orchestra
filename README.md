# Orchestra

A multi-agent orchestrator. Each agent is a self-contained tool that does one job well. This document covers the first agent: **referral-finder**.

---

## Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment Variables](#environment-variables)
  - [Gmail App Password Setup](#gmail-app-password-setup)
- [Referral Finder Agent](#referral-finder-agent)
  - [What It Does](#what-it-does)
  - [Usage](#usage)
  - [All Flags](#all-flags)
  - [Pipeline Walkthrough](#pipeline-walkthrough)
  - [Email Finding — How It Works](#email-finding--how-it-works)
  - [Output Format](#output-format)
- [Adding More Agents](#adding-more-agents)

---

## Overview

You give Orchestra a company name. It finds engineers and tech leads at that company who are based in Israel, tries to find their email address using a chain of free methods, generates a personalized referral request email for each one using Claude, and sends you a single summary email with all the results — ready to copy and send.

No paid email APIs. Everything runs from the command line.

---

## Installation

```bash
# Clone and enter the repo
cd orchestra

# Install dependencies
pip install -e .

# Set up your environment
cp .env.example .env
# then edit .env with your keys (see Configuration below)
```

**Python 3.11+ required.**

---

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and fill in the values below.

#### Required — all agents

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Claude API key from [console.anthropic.com](https://console.anthropic.com) |
| `NOTIFY_EMAIL` | Your email address — where results are sent |
| `SMTP_USER` | Gmail address used to send the notification |
| `SMTP_PASSWORD` | 16-character Gmail App Password (see setup below) |

#### Required — referral-finder agent

| Variable | Description |
|----------|-------------|
| `REFERRAL_FINDER_TAVILY_API_KEY` | Tavily search API key — free tier is 1,000 searches/month, get one at [tavily.com](https://tavily.com) |

#### Optional — referral-finder agent

| Variable | Default | Description |
|----------|---------|-------------|
| `REFERRAL_FINDER_GITHUB_TOKEN` | *(none)* | GitHub personal access token. Without it you get 60 req/hr; with it you get 5,000/hr. Create one at [github.com/settings/tokens](https://github.com/settings/tokens) — no scopes needed, it only reads public data. Strongly recommended. |
| `REFERRAL_FINDER_SMTP_FROM_DOMAIN` | `example.com` | Domain used in the SMTP handshake when verifying email addresses. You don't need to own this domain — `example.com` works fine. |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Claude model used to write the emails. Haiku is cheap and good enough. Use `claude-sonnet-4-6` if you want higher quality drafts. |

#### Optional — global

| Variable | Default | Description |
|----------|---------|-------------|
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

### Gmail App Password Setup

Gmail blocks regular password authentication via SMTP. You need a dedicated App Password instead.

1. Make sure your Gmail account has **2-Step Verification** enabled
2. Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Select **App: Mail** and **Device: Windows Computer**
4. Google generates a 16-character password like `jtbx xyzw abcd efgh`
5. Paste it into your `.env` as `SMTP_PASSWORD=jtbxxxyzwabcdefgh` (spaces optional)

If authentication fails later, the error message in the logs will link you directly back to this page.

---

## Referral Finder Agent

### What It Does

Given only a company name, the agent:

1. Searches LinkedIn via Tavily for engineers and tech leads at that company who are **based in Israel**
2. For each person found, runs a free multi-tier pipeline to find their email address
3. Calls the Claude API to write a short, warm, personalized referral request email — opening with the shared Israeli connection, and mentioning Bar Ilan University if there's a match
4. Sends you one summary email with all results: name, LinkedIn URL, email (or "not found"), and the ready-to-send draft

---

### Usage

```bash
# Dry run — prints everything to the terminal, does NOT send an email
python main.py referral-finder \
  --company "Google" \
  --your-name "Your Name" \
  --your-background "3 years full-stack at a Tel Aviv startup, strong in React and Node" \
  --dry-run

# Full run — sends you a summary email with all results
python main.py referral-finder \
  --company "Google" \
  --your-name "Your Name" \
  --your-background "3 years full-stack at a Tel Aviv startup, strong in React and Node"
```

Always test with `--dry-run` first to verify the output before enabling the email.

---

### All Flags

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--company` | Yes | — | Target company name |
| `--your-name` | Yes | — | Your name, used in the email drafts |
| `--your-background` | Yes | — | 1–2 sentence professional background, quoted |
| `--roles` | No | `Software Engineer` `Senior Engineer` `Tech Lead` `Staff Engineer` | Role keywords to search for. Each role is a separate Tavily query. |
| `--location` | No | `Israel` | Location filter added to every LinkedIn search query |
| `--university` | No | `Bar Ilan University` | Your university. If the person's LinkedIn mentions it, the email will reference the shared connection. |
| `--min-results` | No | `3` | Minimum candidates to find — logs a warning if the search returns fewer |
| `--max-results` | No | `15` | Maximum candidates to process |
| `--dry-run` | No | off | Print results to stdout instead of sending an email |

---

### Pipeline Walkthrough

```
CLI args
   │
   ▼
1. LinkedIn Search (sequential, one query per role)
   Tavily: site:linkedin.com/in "{company}" "{role}" "{location}"
   → parse name + LinkedIn URL from result titles
   → deduplicate by URL and name
   → List[Person]
   │
   ▼ (concurrent — 5 workers via ThreadPoolExecutor)
2. Email Finder            3. Email Generator
   4-tier pipeline   +      Claude API (1 call per person)
   → EmailResult            → str (email body)
   │
   ▼
4. Notifier
   Build summary → send via SMTP (or print if --dry-run)
```

The search is sequential (one Tavily query per role keyword). Steps 2 and 3 run in parallel across all candidates (5 at a time) to keep it fast. If one candidate fails, the rest still complete.

---

### Email Finding — How It Works

Email finding runs through four free tiers in order. It stops at the first confident result. The tiers also **learn from each other**: once an email is found at a company, the pattern (e.g. `{first}.{last}@stripe.com`) is cached to disk in `email_patterns.json`, so the second person at the same company skips straight to Tier 2.

#### Tier 1 — GitHub API

Searches GitHub by name and company. Checks two sources:

- **Profile email field** — many engineers make this public
- **Commit metadata** — scans recent commits from the person's repos; the `commit.author.email` field often contains their work email

Scores each GitHub account by name similarity + company field match. Only accepts a match if the confidence score is ≥ 0.7.

**Free.** 5,000 req/hr with a GitHub token (60/hr without).
**Best for:** active open-source contributors, engineers at software companies.
**Hit rate:** ~30–40% for this audience.

#### Tier 2 — Pattern Cache

If a previous candidate at the same company had their email found (via any tier), the pattern is already known. This tier applies the cached pattern to generate the email address and then verifies it via SMTP.

**Free, unlimited.** Zero API calls once the pattern is known.
**Hit rate:** 100% for subsequent candidates at the same company, once the first is found.

#### Tier 3 — Hunter.io Domain Search

Uses Hunter.io's `/v2/domain-search` endpoint, which returns the company's email domain and most common pattern (e.g. `first.last`). This endpoint is **free and does not count toward the 25/month per-person email finder cap** — it only returns format metadata, not individual emails.

After getting the pattern, the email is constructed and verified via SMTP.

**Hit rate:** Works for ~60% of companies.

#### Tier 4 — SMTP Permutation Scan

Generates 7 common email address permutations:

```
first.last@company.com
firstlast@company.com
f.last@company.com       (first initial + dot + last)
firstl@company.com       (first + last initial)
first@company.com
last.first@company.com
flast@company.com        (first initial + last)
```

For each, performs an **SMTP RCPT TO check** — connects to the company's mail server and asks whether the address exists, without actually sending any email. A `250` response means the address is valid; `550` means it doesn't exist.

Automatically skips companies using Microsoft 365, Mimecast, or Proofpoint because those servers return `250` for everything (catch-all), making verification unreliable.

**Free, no API key, no limits.**
**Limitation:** Doesn't work reliably for Microsoft 365 / enterprise mail providers. Port 25 may be blocked on some cloud providers, but works on most home/office networks.

#### Tier 5 — Not Found

If all tiers fail, the candidate is still included in the results with `Email: NOT FOUND — connect via LinkedIn first`. The Claude-generated message is still produced and included, so you can use it to reach out over LinkedIn instead.

---

### Output Format

#### Dry run (terminal output)

```
======================================================================
DRY RUN — notification email NOT sent. Would have sent:
Subject: Referral Outreach — 8 candidates at Google (5 emails found)
======================================================================

Referral Outreach Results — Google
Run by: Your Name
Candidates: 8 total | 5 with email | 3 email not found

============================================================

=== Alex Cohen — Senior Software Engineer at Google ===
LinkedIn: https://linkedin.com/in/alexcohen
Email:    alex.cohen@google.com  (via github, confidence: high)

Suggested message:
---
Hi Alex,

As a fellow Israeli in tech, I wanted to reach out directly. I'm Your Name,
a full-stack developer with 3 years at a Tel Aviv startup (strong in React
and Node). I'm exploring opportunities at Google and would love to ask for
a referral or hear briefly about your experience on the team. Happy to share
my resume — thanks for any help you can offer.

Best,
Your Name
---

============================================================

=== Dana Levi — Tech Lead at Google ===
LinkedIn: https://linkedin.com/in/danalevi
Email:    NOT FOUND — connect via LinkedIn first

Suggested message:
---
...
---
```

#### Email notification

The exact same content is emailed to `NOTIFY_EMAIL` with the subject:
```
Referral Outreach — 8 candidates at Google (5 emails found)
```

---

## Adding More Agents

Adding a new agent requires only creating a new folder:

```
agents/
└── your-agent-name/
    ├── __init__.py
    ├── agent.py          ← must contain a class inheriting BaseAgent
    └── config.py
```

In `agent.py`:
```python
from shared.base_agent import BaseAgent
from shared.models import AgentResult, AgentRunContext, AgentStatus

class YourAgent(BaseAgent):
    name = "your-agent-name"       # becomes the CLI subcommand
    description = "One line description"

    @classmethod
    def build_arg_parser(cls, subparser): ...   # add argparse args

    @classmethod
    def config_from_args(cls, args): ...        # return your config dataclass

    def run(self, input, ctx: AgentRunContext) -> AgentResult: ...
```

The orchestrator discovers the agent automatically on next run. No registration, no changes to existing code.

Agent-specific secrets follow the naming convention `AGENTNAME_` (uppercase, hyphens → underscores). They are automatically injected from the environment into your config dataclass fields. Example: `YOUR_AGENT_NAME_API_KEY` → `config.api_key`.
