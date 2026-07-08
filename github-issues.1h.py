#!/usr/bin/env python3
import json
import os
import subprocess
import warnings
warnings.filterwarnings("ignore")
from datetime import datetime
import sys
sys.stderr = open(os.devnull, 'w')
os.environ.setdefault("GITHUB_USERNAME", "sabasiddique1")

# Resolve BASE: when run via SwiftBar symlink, use realpath; fallback to project path
_plugin_path = os.environ.get("SWIFTBAR_PLUGIN_PATH") or __file__
_base_candidates = [
    os.path.dirname(os.path.realpath(_plugin_path)),
    os.path.expanduser("~/Desktop/my_Gems/github-issue-pinger"),
]
BASE = next((b for b in _base_candidates if os.path.isfile(os.path.join(b, "github_issue_pinger.py"))), _base_candidates[0])
_venv_python = os.path.join(BASE, ".venv/bin/python3")
PYTHON = os.environ.get("PYTHON_BIN") or (_venv_python if os.path.isfile(_venv_python) else "python3")
SCRIPT = os.path.join(BASE, "github_issue_pinger.py")
TRACKING_SERVER = os.path.join(BASE, "github_issue_tracking_server.py")
TRACKING_FILE = os.environ.get(
    "GITHUB_ISSUE_TRACKING", os.path.join(BASE, "github_issue_tracking.json")
)
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
days_back = data.get("days_back", 7)
report_path = data.get("html_report_path") or os.path.join(BASE, "github_issues_report.html")
report_path = os.path.abspath(os.path.expanduser(report_path))

print(f"🪼 OSS Issues: {total} (last {days_back}d) | refresh=true")
print("---")
print(f"Open full list (browser) | bash=/usr/bin/open param1={report_path} terminal=false")
if os.path.isfile(TRACKING_SERVER):
    print(f"Start activity tracker | bash={PYTHON} param1={TRACKING_SERVER} terminal=true")
print(f"Open tracking file | open={TRACKING_FILE}")
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

    print("Recent issues (last week)")
    for item in items[:max_display]:
        date = short_date(item.get("created_at", ""))
        title = short_title(item.get("title", ""))
        line = f"{date} • {item.get('repo')} #{item.get('number')} {title}"
        print(f"{line} | href={item.get('url')}")
print("---")
print("Open config | open=" + os.path.join(BASE, "github_issue_config.json"))
print("Run now | bash=" + PYTHON + " param1=" + SCRIPT + " terminal=true refresh=true")
print("Open plugin folder | open=/Users/saba/Library/Application Support/SwiftBar/Plugins")


# // killall SwiftBar && open -a SwiftBar
# // killall SwiftBar
# cd /Users/saba/Desktop/my_Gems/github-issue-pinger && tmp=$(mktemp) && python3 -c 'import json,sys; d=json.load(open("github_issue_config.json")); d["request_timeout_seconds"]=30; d["max_runtime_seconds"]=180; json.dump(d, open(sys.argv[1],"w"))' "$tmp" && GITHUB_ISSUE_CONFIG="$tmp" ./.venv/bin/python3 github_issue_pinger.py && open github_issues_report.html && open "swiftbar://refresh" && rm "$tmp"
