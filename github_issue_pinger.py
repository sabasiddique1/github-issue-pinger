#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from github_issue_tracking import (
    TRACKING_FIELDS,
    TRACKING_FIELD_LABELS,
    get_tracking_api_base,
    get_tracking_path,
    issue_key_from_item,
    load_tracking,
)


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.getenv(
    "GITHUB_ISSUE_CONFIG", os.path.join(BASE_DIR, "github_issue_config.json")
)
STATE_PATH = os.getenv(
    "GITHUB_ISSUE_STATE", os.path.join(BASE_DIR, "github_issue_state.json")
)
HTML_REPORT_PATH = os.getenv(
    "GITHUB_ISSUE_HTML", os.path.join(BASE_DIR, "github_issues_report.html")
)
TRACKING_PATH = get_tracking_path()

DEFAULT_CONFIG: Dict[str, Any] = {
    "github_username": "${GITHUB_USERNAME}",
    "github_token": "",
    "include_prs": False,
    "max_issues_per_repo": 10,
    "max_repos": 50,
    "days_back": 7,
    "results_per_page": 100,
    "max_pages_per_repo": 3,
    "connect_timeout_seconds": 5,
    "request_timeout_seconds": 15,
    "request_retries": 3,
    "retry_backoff_seconds": 0.5,
    "max_runtime_seconds": 50,
    "use_parent_issues": True,
    "max_display": 200,
    "refresh_interval_minutes": 60,
}


def ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return dict(default)


def save_json(path: str, data: Dict[str, Any]) -> None:
    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _next_request_timeout(
    request_timeout_seconds: int, deadline_monotonic: Optional[float]
) -> float:
    timeout = max(1, int(request_timeout_seconds))
    if deadline_monotonic is None:
        return float(timeout)
    remaining = deadline_monotonic - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Runtime budget reached")
    return max(1.0, min(float(timeout), remaining))


def build_github_session(
    token: Optional[str],
    request_retries: int,
    retry_backoff_seconds: float,
) -> requests.Session:
    retry_count = max(0, int(request_retries))
    retry = Retry(
        total=retry_count,
        connect=retry_count,
        read=retry_count,
        status=retry_count,
        backoff_factor=max(0.0, float(retry_backoff_seconds)),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session = requests.Session()
    session.headers.update(
        {
            "Accept": "application/vnd.github+json",
            "User-Agent": "github-issue-pinger/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    )
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _label_text_color(bg_hex: str) -> str:
    try:
        r = int(bg_hex[0:2], 16)
        g = int(bg_hex[2:4], 16)
        b = int(bg_hex[4:6], 16)
        lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "#1f2328" if lum > 0.5 else "#ffffff"
    except Exception:
        return "#1f2328"


def _format_date(iso_str: str) -> str:
    import datetime as dt

    try:
        d = dt.datetime.strptime(iso_str[:19], "%Y-%m-%dT%H:%M:%S")
        now = dt.datetime.now(dt.timezone.utc)
        d = d.replace(tzinfo=dt.timezone.utc)
        delta = now - d
        if delta.days == 0:
            return "Today"
        if delta.days == 1:
            return "Yesterday"
        if delta.days < 7:
            return f"{delta.days}d ago"
        return d.strftime("%b %d")
    except Exception:
        return iso_str[:10]


def _escape_html(text: Any) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _repo_from_github_url(url: str) -> str:
    prefix = "https://github.com/"
    if not url.startswith(prefix):
        return ""
    parts = url[len(prefix):].split("/")
    if len(parts) < 4:
        return ""
    return f"{parts[0]}/{parts[1]}"


def _associated_pr_links(item: Dict[str, Any]) -> str:
    associated_prs = item.get("associated_prs", [])
    if not associated_prs:
        return '<span class="label-empty">—</span>'

    links = []
    for pr in associated_prs:
        pr_repo = _escape_html(pr.get("repo", ""))
        pr_number = pr.get("number", "")
        pr_title = _escape_html(pr.get("title", ""))
        pr_url = _escape_html(pr.get("url", ""))
        pr_label = f"{pr_repo}#{pr_number}" if pr_repo and pr_number else pr_title or "PR"
        tooltip = f' title="{pr_title}"' if pr_title else ""
        links.append(
            f'<a class="pr-link" href="{pr_url}" target="_blank"{tooltip}>{pr_label}</a>'
        )
    return "<br />".join(links)


def _tracking_cells(item: Dict[str, Any], tracking: Dict[str, Any]) -> str:
    issue_key = issue_key_from_item(item)
    entry = tracking.get("issues", {}).get(issue_key, {})
    cells = []

    for field in TRACKING_FIELDS:
        checked = " checked" if entry.get(field) else ""
        label = TRACKING_FIELD_LABELS[field]
        cells.append(
            '<td class="track-cell">'
            '<label class="track-check">'
            f'<input type="checkbox" data-track-checkbox data-field="{field}"{checked} '
            f'aria-label="{_escape_html(label)} for {_escape_html(issue_key)}" />'
            '<span aria-hidden="true"></span>'
            "</label>"
            "</td>"
        )

    return "".join(cells)


def write_html_report(
    path: str,
    items: List[Dict[str, Any]],
    days_back: int,
    tracking: Optional[Dict[str, Any]] = None,
) -> None:
    tracking = tracking or {"issues": {}}
    tracking_api_base = get_tracking_api_base()
    rows = []
    for item in items:
        title = _escape_html(item.get("title", ""))
        repo = _escape_html(item.get("repo", ""))
        created = item.get("created_at", "")
        url = _escape_html(item.get("url", ""))
        number = _escape_html(item.get("number", ""))
        issue_key = _escape_html(issue_key_from_item(item))
        labels = item.get("labels", [])
        date_str = _format_date(created)
        repo_short = repo.split("/")[-1] if "/" in repo else repo
        tracking_entry = tracking.get("issues", {}).get(issue_key_from_item(item), {})
        row_class = "has-activity" if any(tracking_entry.get(f) for f in TRACKING_FIELDS) else ""
        label_spans = "".join(
            f'<span class="label" style="background:#{lb.get("color","ededed")};color:{_label_text_color(lb.get("color","ededed"))}">{_escape_html(lb.get("name",""))}</span>'
            for lb in labels
        ) or '<span class="label-empty">—</span>'
        rows.append(
            f'<tr class="{row_class}" data-issue-key="{issue_key}" data-repo="{repo}" '
            f'data-number="{number}" data-title="{title}" data-url="{url}">'
            f'{_tracking_cells(item, tracking)}'
            f'<td class="date">{date_str}</td>'
            f'<td class="repo"><a href="https://github.com/{repo}" target="_blank">{repo_short}</a></td>'
            f'<td class="title"><a href="{url}" target="_blank">{title}</a></td>'
            f'<td class="associated-prs">{_associated_pr_links(item)}</td>'
            f'<td class="labels">{label_spans}</td></tr>'
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>GitHub Issues (last {days_back} days)</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      margin: 0;
      padding: 24px;
      background: #0d1117;
      color: #e6edf3;
      min-height: 100vh;
    }}
    .container {{ max-width: 1320px; margin: 0 auto; }}
    h1 {{
      font-size: 1.5rem;
      font-weight: 600;
      margin: 0 0 20px;
      color: #f0f6fc;
    }}
    .meta {{
      align-items: center;
      color: #8b949e;
      display: flex;
      flex-wrap: wrap;
      font-size: 0.9rem;
      gap: 12px;
      margin-bottom: 20px;
    }}
    .tracking-status {{
      border: 1px solid #30363d;
      border-radius: 999px;
      color: #8b949e;
      font-size: 0.78rem;
      line-height: 1;
      padding: 5px 8px;
    }}
    .tracking-status[data-state="online"] {{ border-color: #238636; color: #7ee787; }}
    .tracking-status[data-state="offline"] {{ border-color: #8b5e34; color: #d29922; }}
    .tracking-status[data-state="error"] {{ border-color: #da3633; color: #ff7b72; }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid #21262d; }}
    th {{
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: #8b949e;
      background: #161b22;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    tr:hover {{ background: #161b22; }}
    tr.has-activity {{ background: rgba(35, 134, 54, 0.07); }}
    tr.has-activity:hover {{ background: rgba(35, 134, 54, 0.12); }}
    .track-cell {{
      padding-left: 8px;
      padding-right: 8px;
      text-align: center;
      width: 52px;
    }}
    .track-check {{
      align-items: center;
      cursor: pointer;
      display: inline-flex;
      height: 22px;
      justify-content: center;
      width: 22px;
    }}
    .track-check input {{
      opacity: 0;
      position: absolute;
    }}
    .track-check span {{
      background: #0d1117;
      border: 1px solid #30363d;
      border-radius: 5px;
      display: block;
      height: 18px;
      position: relative;
      transition: border-color 120ms ease, background 120ms ease;
      width: 18px;
    }}
    .track-check input:focus-visible + span {{
      box-shadow: 0 0 0 3px rgba(88, 166, 255, 0.28);
      outline: none;
    }}
    .track-check input:checked + span {{
      background: #238636;
      border-color: #2ea043;
    }}
    .track-check input:checked + span::after {{
      border: solid #ffffff;
      border-width: 0 2px 2px 0;
      content: "";
      height: 9px;
      left: 6px;
      position: absolute;
      top: 2px;
      transform: rotate(45deg);
      width: 4px;
    }}
    .track-check input:disabled + span {{
      cursor: wait;
      opacity: 0.6;
    }}
    .date {{ white-space: nowrap; color: #8b949e; font-size: 0.85rem; width: 90px; }}
    .repo {{ width: 140px; }}
    .repo a {{
      color: #58a6ff;
      text-decoration: none;
      font-size: 0.9rem;
    }}
    .repo a:hover {{ text-decoration: underline; }}
    .title a {{
      color: #e6edf3;
      text-decoration: none;
      font-size: 0.95rem;
      line-height: 1.4;
    }}
    .title a:hover {{ color: #58a6ff; text-decoration: underline; }}
    .associated-prs {{ width: 220px; font-size: 0.85rem; }}
    .pr-link {{
      color: #79c0ff;
      text-decoration: none;
      line-height: 1.5;
    }}
    .pr-link:hover {{ text-decoration: underline; }}
    .labels {{ max-width: 180px; }}
    .label {{
      display: inline-block;
      padding: 2px 8px;
      border-radius: 12px;
      font-size: 0.75rem;
      font-weight: 500;
      margin-right: 4px;
      margin-bottom: 2px;
    }}
    .label-empty {{ color: #8b949e; font-size: 0.9rem; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>GitHub Issues (last {days_back} days)</h1>
    <p class="meta">
      <span>{len(items)} issues from your forked repos</span>
      <span class="tracking-status" data-tracking-status data-state="checking">Tracking: checking</span>
    </p>
    <table>
      <thead>
        <tr>
          <th>Checked</th>
          <th>Commented</th>
          <th>Made PR</th>
          <th>Date</th>
          <th>Repo</th>
          <th>Title</th>
          <th>Associated PRs</th>
          <th>Labels</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </div>
  <script>
    (() => {{
      const apiBase = {json.dumps(tracking_api_base)};
      const trackingUrl = `${{apiBase}}/tracking`;
      const trackUrl = `${{apiBase}}/track`;
      const status = document.querySelector("[data-tracking-status]");
      const fields = {json.dumps(list(TRACKING_FIELDS))};

      function setStatus(state, text) {{
        if (!status) return;
        status.dataset.state = state;
        status.textContent = text;
      }}

      function setRowState(row) {{
        const hasActivity = fields.some((field) => {{
          const input = row.querySelector(`[data-track-checkbox][data-field="${{field}}"]`);
          return input && input.checked;
        }});
        row.classList.toggle("has-activity", hasActivity);
      }}

      async function loadTrackingState() {{
        try {{
          const response = await fetch(trackingUrl, {{ cache: "no-store" }});
          if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
          const tracking = await response.json();
          const issues = tracking.issues || {{}};

          document.querySelectorAll("tr[data-issue-key]").forEach((row) => {{
            const entry = issues[row.dataset.issueKey] || {{}};
            fields.forEach((field) => {{
              const input = row.querySelector(`[data-track-checkbox][data-field="${{field}}"]`);
              if (!input || input.disabled) return;
              const checked = Boolean(entry[field]);
              input.checked = checked;
              input.dataset.previousChecked = String(checked);
            }});
            setRowState(row);
          }});

          setStatus("online", "Tracking: loaded");
        }} catch (error) {{
          setStatus("offline", "Tracking: server offline");
        }}
      }}

      async function saveCheckbox(input) {{
        const row = input.closest("tr[data-issue-key]");
        if (!row) return;

        const previous = input.dataset.previousChecked === "true";
        const next = input.checked;
        input.disabled = true;
        setStatus("checking", "Tracking: saving");

        const payload = {{
          issue_key: row.dataset.issueKey,
          repo: row.dataset.repo,
          number: row.dataset.number,
          title: row.dataset.title,
          url: row.dataset.url,
          field: input.dataset.field,
          value: next,
        }};

        try {{
          const response = await fetch(trackUrl, {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify(payload),
          }});
          if (!response.ok) {{
            const details = await response.json().catch(() => ({{}}));
            throw new Error(details.error || `HTTP ${{response.status}}`);
          }}
          input.dataset.previousChecked = String(next);
          setRowState(row);
          setStatus("online", "Tracking: saved");
        }} catch (error) {{
          input.checked = previous;
          setRowState(row);
          setStatus("error", "Tracking: save failed");
        }} finally {{
          input.disabled = false;
        }}
      }}

      document.querySelectorAll("[data-track-checkbox]").forEach((input) => {{
        input.dataset.previousChecked = String(input.checked);
        setRowState(input.closest("tr[data-issue-key]"));
        input.addEventListener("change", () => saveCheckbox(input));
      }});

      loadTrackingState();
    }})();
  </script>
</body>
</html>"""

    ensure_parent_dir(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def gh_get(
    session: requests.Session,
    url: str,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float] = None,
) -> Any:
    connect_timeout = max(1.0, float(connect_timeout_seconds))
    read_timeout = _next_request_timeout(request_timeout_seconds, deadline_monotonic)
    try:
        r = session.get(url, timeout=(connect_timeout, read_timeout))
    except requests.exceptions.Timeout as exc:
        raise TimeoutError(
            f"GitHub API request timed out for {url} "
            f"(connect={connect_timeout:.1f}s, read={read_timeout:.1f}s)"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"GitHub API request failed for {url}: {exc}") from exc
    if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
        reset = r.headers.get("X-RateLimit-Reset")
        reset_note = f" (resets at {reset})" if reset else ""
        raise RuntimeError(f"GitHub rate limit exceeded{reset_note}. Add GITHUB_TOKEN.")
    r.raise_for_status()
    return r.json()


def iso_to_epoch(iso_str: str) -> int:
    try:
        return int(time.mktime(time.strptime(iso_str, "%Y-%m-%dT%H:%M:%SZ")))
    except Exception:
        return 0


def list_forked_repos(
    session: requests.Session,
    username: str,
    max_repos: int,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
) -> List[Dict[str, Any]]:
    repos: List[Dict[str, Any]] = []
    page = 1
    while len(repos) < max_repos:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            break
        url = (
            f"https://api.github.com/users/{username}/repos"
            f"?per_page=100&page={page}&sort=updated&direction=desc"
        )
        try:
            batch = gh_get(
                session,
                url,
                connect_timeout_seconds,
                request_timeout_seconds,
                deadline_monotonic,
            )
        except TimeoutError:
            break
        if not batch:
            break
        for repo in batch:
            if repo.get("fork"):
                repos.append(repo)
                if len(repos) >= max_repos:
                    break
        page += 1
    return repos


def resolve_issue_repo(
    session: requests.Session,
    fork_full_name: str,
    use_parent_issues: bool,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
) -> str:
    if not use_parent_issues:
        return fork_full_name
    url = f"https://api.github.com/repos/{fork_full_name}"
    try:
        repo = gh_get(
            session,
            url,
            connect_timeout_seconds,
            request_timeout_seconds,
            deadline_monotonic,
        )
    except TimeoutError:
        return fork_full_name
    parent = repo.get("parent") or repo.get("source")
    if parent and parent.get("full_name"):
        return parent["full_name"]
    return fork_full_name


def fetch_open_issues(
    session: requests.Session,
    repo_full_name: str,
    include_prs: bool,
    max_issues: int,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
) -> List[Dict[str, Any]]:
    url = (
        f"https://api.github.com/repos/{repo_full_name}/issues"
        f"?state=open&per_page={max_issues}&sort=created&direction=desc"
    )
    items = gh_get(
        session,
        url,
        connect_timeout_seconds,
        request_timeout_seconds,
        deadline_monotonic,
    )
    if include_prs:
        return items
    return [it for it in items if "pull_request" not in it]


def fetch_recent_issues(
    session: requests.Session,
    repo_full_name: str,
    include_prs: bool,
    results_per_page: int,
    max_pages: int,
    cutoff_epoch: int,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
) -> List[Dict[str, Any]]:
    recent: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            break
        url = (
            f"https://api.github.com/repos/{repo_full_name}/issues"
            f"?state=open&per_page={results_per_page}&page={page}&sort=created&direction=desc"
        )
        items = gh_get(
            session,
            url,
            connect_timeout_seconds,
            request_timeout_seconds,
            deadline_monotonic,
        )
        if not items:
            break
        if not include_prs:
            items = [it for it in items if "pull_request" not in it]

        oldest_epoch = None
        for it in items:
            created_at = it.get("created_at", "")
            created_epoch = iso_to_epoch(created_at)
            if oldest_epoch is None or created_epoch < oldest_epoch:
                oldest_epoch = created_epoch
            if created_epoch >= cutoff_epoch:
                recent.append(it)

        if oldest_epoch is not None and oldest_epoch < cutoff_epoch:
            break
    return recent


def fetch_associated_prs(
    session: requests.Session,
    repo_full_name: str,
    issue_number: int,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
) -> List[Dict[str, Any]]:
    url = (
        f"https://api.github.com/repos/{repo_full_name}/issues/{issue_number}/timeline"
        "?per_page=100"
    )
    events = gh_get(
        session,
        url,
        connect_timeout_seconds,
        request_timeout_seconds,
        deadline_monotonic,
    )
    associated_prs: List[Dict[str, Any]] = []
    seen = set()

    for event in events:
        source = event.get("source") or {}
        source_issue = source.get("issue") or {}
        if not source_issue.get("pull_request"):
            continue

        pr_number = source_issue.get("number")
        pr_url = source_issue.get("html_url") or ""
        pr_repo = _repo_from_github_url(pr_url) or repo_full_name
        if not pr_number or not pr_url:
            continue

        key = (pr_repo, pr_number)
        if key in seen:
            continue
        seen.add(key)
        associated_prs.append(
            {
                "repo": pr_repo,
                "number": pr_number,
                "title": source_issue.get("title", ""),
                "url": pr_url,
            }
        )

    return associated_prs


def enrich_associated_prs(
    session: requests.Session,
    items: List[Dict[str, Any]],
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
) -> bool:
    skipped_due_to_budget = False

    for item in items:
        item["associated_prs"] = []
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            skipped_due_to_budget = True
            continue

        try:
            item["associated_prs"] = fetch_associated_prs(
                session,
                item.get("repo", ""),
                int(item.get("number", 0)),
                connect_timeout_seconds,
                request_timeout_seconds,
                deadline_monotonic,
            )
        except TimeoutError:
            skipped_due_to_budget = True
        except Exception:
            item["associated_prs"] = []

    return skipped_due_to_budget


def main() -> None:
    cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    state = load_json(STATE_PATH, {"last_seen": {}})

    token = os.getenv("GITHUB_TOKEN") or cfg.get("github_token") or None
    username = os.getenv("GITHUB_USERNAME") or cfg.get("github_username") or ""
    include_prs = bool(cfg.get("include_prs", False))
    max_issues = int(cfg.get("max_issues_per_repo", 10))
    max_repos = int(cfg.get("max_repos", 50))
    days_back = int(cfg.get("days_back", 7))
    results_per_page = int(cfg.get("results_per_page", 100))
    max_pages_per_repo = int(cfg.get("max_pages_per_repo", 3))
    use_parent_issues = bool(cfg.get("use_parent_issues", True))
    connect_timeout_seconds = int(cfg.get("connect_timeout_seconds", 5))
    request_timeout_seconds = int(cfg.get("request_timeout_seconds", 15))
    request_retries = int(cfg.get("request_retries", 3))
    retry_backoff_seconds = float(cfg.get("retry_backoff_seconds", 0.5))
    max_runtime_seconds = int(cfg.get("max_runtime_seconds", 50))
    deadline_monotonic = (
        time.monotonic() + max_runtime_seconds if max_runtime_seconds > 0 else None
    )
    now_epoch = int(time.time())
    cutoff_epoch = now_epoch - (days_back * 24 * 60 * 60)
    session = build_github_session(token, request_retries, retry_backoff_seconds)

    try:
        if not username or username in ("your_github_username", "${GITHUB_USERNAME}"):
            raise SystemExit(
                "Set github_username in github_issue_config.json or GITHUB_USERNAME env var"
            )

        try:
            repos = list_forked_repos(
                session,
                username,
                max_repos,
                connect_timeout_seconds,
                request_timeout_seconds,
                deadline_monotonic,
            )
        except Exception as exc:
            output = {
                "total_new": 0,
                "items": [],
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "error": str(exc),
            }
            print(json.dumps(output, indent=2))
            return

        new_items: List[Dict[str, Any]] = []
        total_recent = 0
        per_repo_counts: Dict[str, int] = {}
        processed_repos = 0
        partial = False
        warning = ""

        for repo in repos:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                partial = True
                warning = (
                    f"Runtime budget reached after processing {processed_repos} repos; "
                    "results are partial."
                )
                break
            fork_full_name = repo.get("full_name")
            if not fork_full_name:
                continue
            try:
                issue_repo = resolve_issue_repo(
                    session,
                    fork_full_name,
                    use_parent_issues,
                    connect_timeout_seconds,
                    request_timeout_seconds,
                    deadline_monotonic,
                )
                issues = fetch_recent_issues(
                    session,
                    issue_repo,
                    include_prs,
                    results_per_page,
                    max_pages_per_repo,
                    cutoff_epoch,
                    connect_timeout_seconds,
                    request_timeout_seconds,
                    deadline_monotonic,
                )
            except TimeoutError:
                partial = True
                warning = (
                    f"Runtime budget reached after processing {processed_repos} repos; "
                    "results are partial."
                )
                break
            except Exception as exc:
                output = {
                    "total_new": 0,
                    "items": [],
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "error": str(exc),
                }
                print(json.dumps(output, indent=2))
                return

            newest_epoch = 0
            repo_recent = 0
            for it in issues:
                created_at = it.get("created_at", "")
                created_epoch = iso_to_epoch(created_at)
                if created_epoch > newest_epoch:
                    newest_epoch = created_epoch
                if created_epoch >= cutoff_epoch:
                    repo_recent += 1
                    total_recent += 1
                    labels = [
                        {"name": lb.get("name", ""), "color": lb.get("color", "ededed")}
                        for lb in it.get("labels", [])
                    ]
                    new_items.append(
                        {
                            "repo": issue_repo,
                            "source_repo": fork_full_name,
                            "number": it.get("number"),
                            "title": it.get("title"),
                            "url": it.get("html_url"),
                            "created_at": created_at,
                            "labels": labels,
                        }
                    )

            per_repo_counts[issue_repo] = repo_recent
            state["last_seen"][issue_repo] = newest_epoch
            processed_repos += 1

        new_items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        skipped_associated_pr_lookup = enrich_associated_prs(
            session,
            new_items,
            connect_timeout_seconds,
            request_timeout_seconds,
            deadline_monotonic,
        )
        tracking = load_tracking(TRACKING_PATH)

        save_json(STATE_PATH, state)
        write_html_report(HTML_REPORT_PATH, new_items, days_back, tracking)

        output = {
            "total_recent": total_recent,
            "per_repo_counts": per_repo_counts,
            "days_back": days_back,
            "max_display": int(cfg.get("max_display", 200)),
            "html_report_path": HTML_REPORT_PATH,
            "tracking_path": TRACKING_PATH,
            "items": new_items,
            "processed_repos": processed_repos,
            "fetched_fork_repos": len(repos),
            "partial": partial,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if skipped_associated_pr_lookup:
            pr_warning = "Associated PR lookup was skipped for some issues due to runtime budget."
            warning = f"{warning} {pr_warning}".strip() if warning else pr_warning
        if warning:
            output["warning"] = warning
        print(json.dumps(output, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
