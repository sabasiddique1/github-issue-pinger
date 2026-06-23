#!/usr/bin/env python3
import json
import os
import subprocess
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
import sys
sys.stderr = open(os.devnull, 'w')
os.environ.setdefault("GITHUB_USERNAME", "")

# Resolve BASE: when run via SwiftBar symlink, use realpath; fallback to project path
_plugin_path = os.environ.get("SWIFTBAR_PLUGIN_PATH") or __file__
_base_candidates = [
    os.path.dirname(os.path.realpath(_plugin_path)),
    os.path.dirname(os.path.realpath(__file__)),
]
BASE = next((b for b in _base_candidates if os.path.isfile(os.path.join(b, "github_issue_pinger.py"))), _base_candidates[0])
_venv_python = os.path.join(BASE, ".venv/bin/python3")
PYTHON = os.environ.get("PYTHON_BIN") or (_venv_python if os.path.isfile(_venv_python) else "python3")
SCRIPT = os.path.join(BASE, "github_issue_pinger.py")
SWIFTBAR_PLUGINS_DIR = os.path.expanduser("~/Library/Application Support/SwiftBar/Plugins")
SUBPROCESS_TIMEOUT_SECONDS = 75

try:
    out = subprocess.check_output([PYTHON, SCRIPT], timeout=SUBPROCESS_TIMEOUT_SECONDS).decode("utf-8")
    data = json.loads(out)
except subprocess.TimeoutExpired:
    data = {
        "error": (
            f"Issue fetch exceeded {SUBPROCESS_TIMEOUT_SECONDS}s. "
            "Reduce max_repos/max_pages_per_repo or set max_runtime_seconds in github_issue_config.json."
        ),
        "total_recent": 0,
        "items": [],
        "days_back": 7,
    }
except (subprocess.CalledProcessError, FileNotFoundError, json.JSONDecodeError) as e:
    data = {"error": str(e), "total_recent": 0, "items": [], "days_back": 7}


def short_date(iso_str: str) -> str:
    try:
        dt = datetime.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")
        return dt.strftime("%b %d")
    except Exception:
        return ""


def short_title(text: str, limit: int = 80) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."

total = data.get("total_recent", 0)
total_new = data.get("total_new", 0)
days_back = data.get("days_back", 7)
report_path = data.get("html_report_path") or os.path.join(BASE, "github_issues_report.html")
report_path = os.path.abspath(os.path.expanduser(report_path))

if total_new:
    title = f"🪼 OSS Issues: {total_new} new / {total} (last {days_back}d) | refresh=true"
else:
    title = f"🪼 OSS Issues: {total} (last {days_back}d) | refresh=true"
print(title)
print("---")
print(f"Open full list (browser) | bash=/usr/bin/open param1={report_path} terminal=false")
print("---")
if data.get("error"):
    print(f"Error: {data['error']}")
    print("---")
    print("Open config | open=" + os.path.join(BASE, "github_issue_config.json"))
    print("Run now | bash=" + PYTHON + " param1=" + SCRIPT + " terminal=true refresh=true")
elif not data.get("items"):
    print(f"No issues opened in last {days_back} days")
    if data.get("warning"):
        print(f"Warning: {short_title(str(data['warning']), 120)}")
    print("---")
    print("Tip: increase days_back or max_pages_per_repo")
else:
    items = data.get("items", [])
    counts = data.get("per_repo_counts", {})
    max_display = data.get("max_display", 200)

    # Compact repo summary: "pandas (7) • sklearn (2) • meteor (2)" in 1-2 lines
    repo_parts = []
    for repo, count in sorted(counts.items(), key=lambda x: x[1], reverse=True):
        if count > 0:
            short = repo.split("/")[-1] if "/" in repo else repo
            repo_parts.append(f"{short} ({count})")
    if repo_parts:
        compact = " • ".join(repo_parts[:10])  # max 10 repos in summary
        if len(repo_parts) > 10:
            compact += f" +{len(repo_parts) - 10} more"
        print(f"Repos: {compact} | bash=/usr/bin/open param1={report_path} terminal=false")
    if data.get("warning"):
        print(f"Warning: {short_title(str(data['warning']), 120)}")
    print("---")

    print("Recent issues (last {} days)".format(days_back))
    for item in items[:max_display]:
        date = short_date(item.get("created_at", ""))
        title = short_title(item.get("title", ""))
        prefix = "NEW • " if item.get("is_new") else ""
        line = f"{prefix}{date} • {item.get('repo')} #{item.get('number')} {title}"
        print(f"{line} | href={item.get('url')}")
print("---")
print("Open .env | open=" + os.path.join(BASE, ".env"))
print("Open config | open=" + os.path.join(BASE, "github_issue_config.json"))
print("Run now | bash=" + PYTHON + " param1=" + SCRIPT + " terminal=true refresh=true")
print("Open plugin folder | open=" + SWIFTBAR_PLUGINS_DIR)


# // killall SwiftBar && open -a SwiftBar
# // killall SwiftBar