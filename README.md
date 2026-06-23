# GitHub Issue Pinger

Monitor **recent open issues** across GitHub repositories you have **forked**, with upstream-aware discovery, JSON output, an HTML report, and optional **SwiftBar** menu bar integration for macOS.

Built for developers who contribute to open source through forks and need a lightweight way to spot new upstream activity without watching dozens of repos manually.

---

## Features

- **Fork-aware scanning** — lists your forked repositories and queries issues from the **upstream parent** when available
- **Time-window filtering** — surfaces open issues created within a configurable lookback window (default: 7 days)
- **New-issue detection** — tracks `last_seen` per upstream repo and flags issues that appeared since the previous run
- **Concurrent fetching** — parallel GitHub API requests with connection pooling, retries, and runtime budgets
- **Rate-limit safety** — authenticated requests via `GITHUB_TOKEN`; automatic concurrency throttling when unauthenticated
- **Multiple outputs** — structured JSON to stdout, persistent state file, and a styled HTML report
- **SwiftBar plugin** — hourly menu bar refresh with issue counts, repo summaries, and quick links

---

## Architecture

```text
┌─────────────────────┐     subprocess      ┌──────────────────────────┐
│  github-issues.1h.py│ ──────────────────► │  github_issue_pinger.py  │
│  (SwiftBar plugin)  │                     │  (core fetcher)          │
└─────────────────────┘                     └────────────┬─────────────┘
                                                         │
                         ┌───────────────────────────────┼───────────────────────────────┐
                         ▼                               ▼                               ▼
                 GitHub REST API              github_issue_state.json          github_issues_report.html
```

| Component | Responsibility |
|-----------|----------------|
| `github_issue_pinger.py` | GitHub API client, issue aggregation, state persistence, HTML report |
| `github-issues.1h.py` | SwiftBar entry point; invokes the fetcher and renders menu output |
| `github_issue_config.json` | Non-secret runtime tuning (limits, timeouts, concurrency) |
| `.env` | Secrets: `GITHUB_USERNAME`, `GITHUB_TOKEN` |
| `github_issue_state.json` | Generated locally; stores per-repo `last_seen` timestamps |

There is **no background daemon**. Each run is a single batch fetch triggered manually, on a schedule (SwiftBar), or from automation.

---

## Requirements

- **macOS** (SwiftBar integration) or any OS for the standalone Python fetcher
- **Python 3.11+**
- **GitHub personal access token** with `public_repo` scope (or `repo` for private forks)

Use Homebrew Python on macOS. The system Python shipped with Xcode Command Line Tools is linked against LibreSSL and commonly triggers `urllib3` warnings.

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/Mahnoor-Zaffar/github-issue-pinger.git
cd github-issue-pinger

brew install python@3.11
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Edit `.env`:

```bash
GITHUB_USERNAME=your-github-username
GITHUB_TOKEN=ghp_your_token_here
```

Create a token at [github.com/settings/tokens](https://github.com/settings/tokens).  
**Required scope:** `public_repo` (use `repo` if you fork private repositories).

Non-secret defaults live in `github_issue_config.json`. Override paths with environment variables if needed (see [Configuration](#configuration)).

### 3. Run

```bash
source .venv/bin/activate
python github_issue_pinger.py
```

Expected behavior:

- JSON summary printed to stdout
- `github_issues_report.html` written in the project root
- `github_issue_state.json` updated locally (gitignored)

Verify authentication in the JSON output:

```json
"authenticated": true
```

---

## Configuration

### Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITHUB_USERNAME` | Yes | GitHub account whose **forks** are scanned |
| `GITHUB_TOKEN` | Strongly recommended | Personal access token for higher rate limits |
| `GITHUB_ISSUE_CONFIG` | No | Path to JSON config (default: `./github_issue_config.json`) |
| `GITHUB_ISSUE_STATE` | No | Path to state file (default: `./github_issue_state.json`) |
| `GITHUB_ISSUE_HTML` | No | Path to HTML report (default: `./github_issues_report.html`) |
| `GITHUB_ISSUE_DOTENV` | No | Path to `.env` file (default: `./.env`) |
| `PYTHON_BIN` | No | Python interpreter for SwiftBar (default: `.venv/bin/python3`) |

### `github_issue_config.json`

| Key | Default | Description |
|-----|---------|-------------|
| `days_back` | `3` | Only include issues created within this many days |
| `max_repos` | `20` | Maximum forked repos to scan |
| `max_pages_per_repo` | `3` | Issue list pages per upstream repo |
| `results_per_page` | `100` | GitHub API page size |
| `max_concurrent_repos` | `8` | Parallel repo workers (capped at `2` without a token) |
| `max_runtime_seconds` | `50` | Hard runtime budget per fetch |
| `use_parent_issues` | `true` | Query upstream parent instead of the fork |
| `include_prs` | `false` | Include pull requests in results |
| `refresh_interval_minutes` | `60` | Documented default for scheduled refresh (SwiftBar uses 1h via filename) |

---

## Output reference

### JSON (stdout)

Key fields:

| Field | Meaning |
|-------|---------|
| `total_recent` | Issues in the lookback window |
| `total_new` | Issues newer than `last_seen` for that upstream repo |
| `authenticated` | Whether `GITHUB_TOKEN` was used |
| `items[].is_new` | `true` for issues not seen on the previous run |
| `partial` | `true` if the runtime budget was exceeded |
| `warning` | Non-fatal issues (missing token, partial results, etc.) |

**First run per repo:** establishes a baseline — issues are not marked `is_new` until a subsequent fetch.

### HTML report

Open `github_issues_report.html` in a browser. New issues are highlighted with a **NEW** badge.

---

## SwiftBar setup (macOS menu bar)

1. Install [SwiftBar](https://swiftbar.app/).
2. Symlink the plugin into your SwiftBar plugins directory:

```bash
ln -sf "$(pwd)/github-issues.1h.py" \
  "$HOME/Library/Application Support/SwiftBar/Plugins/github-issues.1h.py"
```

3. Restart SwiftBar:

```bash
killall SwiftBar 2>/dev/null; open -a SwiftBar
```

The `.1h.py` suffix tells SwiftBar to refresh **every hour**. The menu bar shows counts like `3 new / 228 (last 7d)` when new issues exist.

Ensure `.env` lives in the same directory as `github_issue_pinger.py` so the fetcher loads credentials when invoked by SwiftBar.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `"authenticated": false` | Missing or invalid token | Set `GITHUB_TOKEN` in `.env` |
| Rate limit errors | No token + many forks | Add a token; reduce `max_repos` or `max_concurrent_repos` |
| `"partial": true` | Runtime budget exceeded | Increase `max_runtime_seconds` or reduce `max_repos` / `max_pages_per_repo` |
| SwiftBar timeout | Fetch exceeds 75s | Same as above; tune config |
| `total_new` always 0 | Baseline run or no new upstream issues | Normal on first run; re-check after new issues are opened |
| Wrong repos scanned | Incorrect username | `GITHUB_USERNAME` must own the forks |

---

## Security

- **Never commit** `.env`, tokens, or generated state/report files.
- Tokens belong in `.env` only — not in `github_issue_config.json`.
- Use the minimum token scope required (`public_repo` for public forks).
- Rotate tokens if exposed.

---

## Project layout

```text
github-issue-pinger/
├── .github/workflows/daily-fetch.yml
├── github_issue_pinger.py    # Core fetcher
├── github-issues.1h.py       # SwiftBar plugin
├── github_issue_config.json  # Non-secret defaults
├── .env.example              # Credential template
├── requirements.txt          # Python dependencies
└── pyproject.toml            # Tooling (Ruff, Pyright)
```

---

## Scheduled fetch (GitHub Actions)

A daily workflow runs at **08:00 UTC** and uploads the HTML report, JSON summary, and state file as workflow artifacts.

Issue `last_seen` state is cached between runs via GitHub Actions cache, so `total_new` reflects issues since the previous scheduled fetch in CI.

### Repository secrets required

Configure these under **Settings → Secrets and variables → Actions**:

| Secret name | Value |
|-------------|-------|
| `USERNAME` | GitHub account whose forks are scanned (e.g. `Mahnoor-Zaffar`) |
| `TOKEN` | Personal access token (`public_repo` scope) |

The workflow maps these to `GITHUB_USERNAME` and `GITHUB_TOKEN` for the Python fetcher. Local `.env` still uses the `GITHUB_*` variable names.

Trigger manually from the **Actions** tab via **Run workflow**, or wait for the daily schedule.

Artifacts are retained for 30 days under the workflow run summary.

---

## Development

```bash
source .venv/bin/activate
pip install -r requirements.txt
python github_issue_pinger.py | python -m json.tool
```

Type checking and linting are configured in `pyproject.toml` for use with Pyright and Ruff.

---

## License

No license file is included yet. Add one before distributing or accepting external contributions.

---

## Acknowledgements

Forked from [sabasiddique1/github-issue-pinger](https://github.com/sabasiddique1/github-issue-pinger).