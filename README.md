# GitHub Issue Pinger

GitHub Issue Pinger now ships as a **Tauri + React macOS menu bar app** with the original **Python script** and **SwiftBar plugin** still available.

It tracks **open issues** from repositories you have **forked** on GitHub, prefers the **upstream repo** for issue discovery, writes an **HTML report**, and keeps a locally cached result so the menu bar app stays responsive while idle.

## What is in this repo

- `github_issue_pinger.py`: standalone Python fetcher that prints JSON and writes the HTML report.
- `github-issues.1h.py`: optional SwiftBar plugin wrapper around the Python fetcher.
- `src/`: React UI used by the Tauri app window.
- `src-tauri/`: Tauri desktop shell, tray integration, scheduling, caching, and Python bridge.
- `github_issue_config.json`: checked-in sample config with no token.
- `.env.example`: development-only secret template.

## Security and config

Secrets are no longer stored in the checked-in JSON config.

1. Copy the env template:

```bash
cp .env.example .env
```

2. Fill in:

```bash
GITHUB_USERNAME=your-github-username
GITHUB_TOKEN=your-github-token
```

3. Keep `github_issue_config.json` for non-secret defaults only. The Tauri app copies this file into the app data directory on first run and the Python script still accepts `GITHUB_ISSUE_CONFIG` if you want a custom path.

Environment variables used by the fetcher:

- `GITHUB_USERNAME`
- `GITHUB_TOKEN`
- `GITHUB_ISSUE_CONFIG`
- `GITHUB_ISSUE_STATE`
- `GITHUB_ISSUE_HTML`
- `PYTHON_BIN`

## Install dependencies

### Python

Use a Homebrew Python on macOS. The Apple Command Line Tools Python is commonly linked against LibreSSL, which causes `urllib3` warnings and is not the intended runtime for this project.

```bash
brew install python@3.11
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install requests
```

### Node.js

Install a current Node.js release, then install frontend and Tauri JS dependencies:

```bash
npm install
```

### Rust

Tauri requires the Rust toolchain:

```bash
curl https://sh.rustup.rs -sSf | sh
rustup default stable
```

## Run in dev mode

### Standalone Python fetcher

This is still the source of truth for the data fetch and remains fully usable on its own:

```bash
source .venv/bin/activate
python github_issue_pinger.py
```

The script prints JSON to stdout and writes `github_issues_report.html`.

### Activity tracking checkboxes

The generated HTML report includes local activity checkboxes for each issue:

- `Checked`
- `Commented`
- `Made PR`

Start the local tracking server before clicking checkboxes:

```bash
source .venv/bin/activate
python github_issue_tracking_server.py
```

Then generate and open the report in another terminal:

```bash
source .venv/bin/activate
python github_issue_pinger.py
open github_issues_report.html
```

Checkbox changes are saved to `github_issue_tracking.json`. Regenerating the report reads that file and restores the saved checkbox states. Clicking a checkbox never removes an issue from the report.

### React frontend only

```bash
npm run dev
```

### Full Tauri menu bar app

```bash
npm run tauri:dev
```

## Test menu bar changes

Use `npm run tauri:dev`, then validate:

1. The tray icon appears with a title next to it.
2. The tray menu contains `Open`, `Refresh`, `Open Report`, and `Quit`.
3. The app fetches once on startup.
4. Manual `Refresh` triggers a fetch but repeated clicks do not create overlapping jobs.
5. Closing the window hides it instead of quitting the app.
6. `Open Report` launches the generated HTML report.
7. Idle CPU stays effectively at zero after the scheduled fetch settles.

Note: in development you may still see normal app-window behavior from the debugger/toolchain. The dock icon is intentionally hidden only for non-debug macOS builds.

## Build the production app

```bash
npm run tauri:build
```

Expected output location:

- `src-tauri/target/release/bundle/`

## Tauri app behavior

- Default refresh interval: `60` minutes
- Minimum enforced refresh interval: `30` minutes
- Fetch triggers:
  - startup
  - manual refresh
  - scheduled interval
- No aggressive polling loop
- No overlapping fetch jobs
- Cached latest result written locally
- Tray updates from cached/live state
- Dock icon hidden on macOS production builds

The Tauri shell keeps app state in the app-local data directory. On macOS this resolves under:

- `~/Library/Application Support/com.sabasiddique.github-issue-pinger/`

That directory holds the working config copy, fetch cache, state JSON, and generated HTML report used by the desktop app.

## SwiftBar support

The SwiftBar plugin is still available and now respects `PYTHON_BIN` if you want to point it at a non-default interpreter:

```bash
PYTHON_BIN=/absolute/path/to/python3
```

If `PYTHON_BIN` is not set, the plugin prefers `.venv/bin/python3` and falls back to `python3`.

## Notes on generated files

The repo now ignores local/generated artifacts such as:

- `.env`
- `.venv/`
- `__pycache__/`
- `.DS_Store`
- `node_modules/`
- `dist/`
- `src-tauri/target/`
- `github_issue_state.json`
- `github_issues_report.html`

Do not commit tokens or generated local state.
