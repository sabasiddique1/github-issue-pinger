#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable, Dict, List, Optional, Tuple, TypedDict

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


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
DOTENV_PATH = os.getenv("GITHUB_ISSUE_DOTENV", os.path.join(BASE_DIR, ".env"))

UNAUTH_MAX_CONCURRENT_REPOS = 2
UNAUTH_RATE_LIMIT_THRESHOLD = 8


class IssueItem(TypedDict, total=False):
    repo: str
    source_repo: str
    number: int
    title: str
    url: str
    created_at: str
    labels: List[Dict[str, str]]
    is_new: bool


class FetchOutput(TypedDict, total=False):
    total_recent: int
    total_new: int
    per_repo_counts: Dict[str, int]
    days_back: int
    max_display: int
    html_report_path: str
    items: List[IssueItem]
    processed_repos: int
    fetched_fork_repos: int
    partial: bool
    generated_at: str
    warning: str
    repo_errors: List[str]
    authenticated: bool
    error: str


DEFAULT_CONFIG: Dict[str, Any] = {
    "github_username": "${GITHUB_USERNAME}",
    "github_token": "",
    "include_prs": False,
    "max_issues_per_repo": 10,
    "max_repos": 20,
    "days_back": 3,
    "results_per_page": 100,
    "max_pages_per_repo": 3,
    "connect_timeout_seconds": 5,
    "request_timeout_seconds": 15,
    "request_retries": 3,
    "retry_backoff_seconds": 0.5,
    "max_runtime_seconds": 50,
    "max_concurrent_repos": 8,
    "use_parent_issues": True,
    "max_display": 200,
    "refresh_interval_minutes": 60,
}


class RateLimitGuard:
    """Tracks GitHub rate-limit headers across concurrent workers."""

    def __init__(self, threshold: int = 100) -> None:
        self._lock = threading.Lock()
        self._remaining: Optional[int] = None
        self.threshold = threshold

    def note(self, response: requests.Response) -> None:
        raw = response.headers.get("X-RateLimit-Remaining")
        if raw is None:
            return
        try:
            remaining = int(raw)
        except ValueError:
            return
        with self._lock:
            self._remaining = remaining

    def pause_if_low(self, threshold: int = 100) -> None:
        with self._lock:
            remaining = self._remaining
        if remaining is not None and remaining < threshold:
            time.sleep(0.5 if threshold <= 10 else 0.25)


def load_dotenv(path: str) -> None:
    """Load KEY=VALUE pairs from a .env file without overwriting existing env vars."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        return


def resolve_github_token(cfg: Dict[str, Any]) -> Optional[str]:
    token = os.getenv("GITHUB_TOKEN") or cfg.get("github_token") or None
    if not token:
        return None
    token = str(token).strip()
    return token or None


def effective_concurrency(configured: int, authenticated: bool) -> int:
    configured = max(1, int(configured))
    if authenticated:
        return configured
    return min(configured, UNAUTH_MAX_CONCURRENT_REPOS)


def rate_limit_threshold(authenticated: bool) -> int:
    return 100 if authenticated else UNAUTH_RATE_LIMIT_THRESHOLD


def make_thread_local_session_factory(
    token: Optional[str],
    request_retries: int,
    retry_backoff_seconds: float,
    pool_size: int,
) -> Callable[[], requests.Session]:
    local = threading.local()

    def factory() -> requests.Session:
        session = getattr(local, "session", None)
        if session is None:
            session = build_github_session(
                token, request_retries, retry_backoff_seconds, pool_size=pool_size
            )
            local.session = session
        return session

    return factory


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
    pool_size: int = 10,
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
    pool = max(4, int(pool_size))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=pool, pool_maxsize=pool)
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


def write_html_report(path: str, items: List[Dict[str, Any]], days_back: int) -> None:
    rows = []
    for item in items:
        title = item.get("title", "").replace("<", "&lt;").replace(">", "&gt;")
        repo = item.get("repo", "").replace("<", "&lt;").replace(">", "&gt;")
        created = item.get("created_at", "")
        url = item.get("url", "")
        labels = item.get("labels", [])
        date_str = _format_date(created)
        repo_short = repo.split("/")[-1] if "/" in repo else repo
        label_spans = "".join(
            f'<span class="label" style="background:#{lb.get("color","ededed")};color:{_label_text_color(lb.get("color","ededed"))}">{lb.get("name","")}</span>'
            for lb in labels
        ) or '<span class="label-empty">—</span>'
        new_badge = '<span class="new-badge">NEW</span> ' if item.get("is_new") else ""
        rows.append(
            f'<tr class="{"is-new" if item.get("is_new") else ""}"><td class="date">{date_str}</td>'
            f'<td class="repo"><a href="https://github.com/{repo}" target="_blank">{repo_short}</a></td>'
            f'<td class="title">{new_badge}<a href="{url}" target="_blank">{title}</a></td>'
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
    .container {{ max-width: 960px; margin: 0 auto; }}
    h1 {{
      font-size: 1.5rem;
      font-weight: 600;
      margin: 0 0 20px;
      color: #f0f6fc;
    }}
    .meta {{ color: #8b949e; font-size: 0.9rem; margin-bottom: 20px; }}
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
    tr.is-new {{ background: rgba(56, 139, 253, 0.08); }}
    .new-badge {{
      display: inline-block;
      margin-right: 6px;
      padding: 1px 6px;
      border-radius: 10px;
      background: #238636;
      color: #ffffff;
      font-size: 0.65rem;
      font-weight: 700;
      letter-spacing: 0.04em;
      vertical-align: middle;
    }}
  </style>
</head>
<body>
  <div class="container">
    <h1>GitHub Issues (last {days_back} days)</h1>
    <p class="meta">{len(items)} issues from your forked repos</p>
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Repo</th>
          <th>Title</th>
          <th>Labels</th>
        </tr>
      </thead>
      <tbody>
        {''.join(rows)}
      </tbody>
    </table>
  </div>
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
    rate_limit_guard: Optional[RateLimitGuard] = None,
) -> Any:
    if rate_limit_guard is not None:
        rate_limit_guard.pause_if_low(rate_limit_guard.threshold)
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
    if rate_limit_guard is not None:
        rate_limit_guard.note(r)
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
    rate_limit_guard: Optional[RateLimitGuard] = None,
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
                rate_limit_guard,
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


def resolve_issue_repo_from_fork(
    fork_repo: Dict[str, Any],
    fork_full_name: str,
    use_parent_issues: bool,
) -> str:
    if not use_parent_issues:
        return fork_full_name
    parent = fork_repo.get("parent") or fork_repo.get("source")
    if parent and parent.get("full_name"):
        return parent["full_name"]
    return fork_full_name


def resolve_issue_repo(
    session: requests.Session,
    fork_full_name: str,
    use_parent_issues: bool,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
    rate_limit_guard: Optional[RateLimitGuard] = None,
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
            rate_limit_guard,
        )
    except TimeoutError:
        return fork_full_name
    parent = repo.get("parent") or repo.get("source")
    if parent and parent.get("full_name"):
        return parent["full_name"]
    return fork_full_name


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
    rate_limit_guard: Optional[RateLimitGuard] = None,
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
            rate_limit_guard,
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


def process_fork_repo(
    fork_repo: Dict[str, Any],
    session_factory: Callable[[], requests.Session],
    rate_limit_guard: RateLimitGuard,
    include_prs: bool,
    results_per_page: int,
    max_pages_per_repo: int,
    cutoff_epoch: int,
    use_parent_issues: bool,
    connect_timeout_seconds: int,
    request_timeout_seconds: int,
    deadline_monotonic: Optional[float],
    last_seen: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], int, int, str, int, Optional[str]]:
    fork_full_name = fork_repo.get("full_name")
    if not fork_full_name:
        return [], 0, 0, "", 0, None

    session = session_factory()
    issue_repo = resolve_issue_repo_from_fork(
        fork_repo, fork_full_name, use_parent_issues
    )
    if use_parent_issues and issue_repo == fork_full_name:
        parent = fork_repo.get("parent") or fork_repo.get("source")
        if not (parent and parent.get("full_name")):
            issue_repo = resolve_issue_repo(
                session,
                fork_full_name,
                use_parent_issues,
                connect_timeout_seconds,
                request_timeout_seconds,
                deadline_monotonic,
                rate_limit_guard,
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
        rate_limit_guard,
    )

    repo_items: List[Dict[str, Any]] = []
    newest_epoch = 0
    repo_recent = 0
    repo_new = 0
    last_seen_epoch = int(last_seen.get(issue_repo, 0) or 0)
    for it in issues:
        created_at = it.get("created_at", "")
        created_epoch = iso_to_epoch(created_at)
        if created_epoch > newest_epoch:
            newest_epoch = created_epoch
        if created_epoch >= cutoff_epoch:
            repo_recent += 1
            is_new = last_seen_epoch > 0 and created_epoch > last_seen_epoch
            if is_new:
                repo_new += 1
            labels = [
                {"name": lb.get("name", ""), "color": lb.get("color", "ededed")}
                for lb in it.get("labels", [])
            ]
            repo_items.append(
                {
                    "repo": issue_repo,
                    "source_repo": fork_full_name,
                    "number": it.get("number"),
                    "title": it.get("title"),
                    "url": it.get("html_url"),
                    "created_at": created_at,
                    "labels": labels,
                    "is_new": is_new,
                }
            )

    return repo_items, repo_recent, repo_new, issue_repo, newest_epoch, None


def main() -> None:
    load_dotenv(DOTENV_PATH)
    cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
    state = load_json(STATE_PATH, {"last_seen": {}})
    last_seen_snapshot: Dict[str, int] = {
        str(repo): int(epoch or 0) for repo, epoch in state.get("last_seen", {}).items()
    }

    token = resolve_github_token(cfg)
    authenticated = token is not None
    username = os.getenv("GITHUB_USERNAME") or cfg.get("github_username") or ""
    include_prs = bool(cfg.get("include_prs", False))
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
    max_concurrent_repos = effective_concurrency(
        int(cfg.get("max_concurrent_repos", 8)), authenticated
    )
    deadline_monotonic = (
        time.monotonic() + max_runtime_seconds if max_runtime_seconds > 0 else None
    )
    now_epoch = int(time.time())
    cutoff_epoch = now_epoch - (days_back * 24 * 60 * 60)
    rate_limit_guard = RateLimitGuard(threshold=rate_limit_threshold(authenticated))
    session = build_github_session(
        token,
        request_retries,
        retry_backoff_seconds,
        pool_size=max_concurrent_repos,
    )
    session_factory = make_thread_local_session_factory(
        token,
        request_retries,
        retry_backoff_seconds,
        pool_size=max_concurrent_repos,
    )

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
                rate_limit_guard,
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
        total_new = 0
        per_repo_counts: Dict[str, int] = {}
        processed_repos = 0
        partial = False
        warning = ""
        if not authenticated:
            warning = (
                "GITHUB_TOKEN is not set; using unauthenticated API limits "
                f"({UNAUTH_MAX_CONCURRENT_REPOS} concurrent repos max). "
                "Copy .env.example to .env and add a token."
            )
        repo_errors: List[str] = []

        pending: Dict[Future, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=max_concurrent_repos) as executor:
            for repo in repos:
                if (
                    deadline_monotonic is not None
                    and time.monotonic() >= deadline_monotonic
                ):
                    partial = True
                    warning = (
                        f"Runtime budget reached after processing {processed_repos} repos; "
                        "results are partial."
                    )
                    break
                pending[
                    executor.submit(
                        process_fork_repo,
                        repo,
                        session_factory,
                        rate_limit_guard,
                        include_prs,
                        results_per_page,
                        max_pages_per_repo,
                        cutoff_epoch,
                        use_parent_issues,
                        connect_timeout_seconds,
                        request_timeout_seconds,
                        deadline_monotonic,
                        last_seen_snapshot,
                    )
                ] = repo

            while pending:
                done, _ = wait(tuple(pending.keys()), return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    try:
                        repo_items, repo_recent, repo_new, issue_repo, newest_epoch, _ = (
                            future.result()
                        )
                    except TimeoutError:
                        partial = True
                        warning = (
                            f"Runtime budget reached after processing {processed_repos} repos; "
                            "results are partial."
                        )
                        pending.clear()
                        break
                    except Exception as exc:
                        repo_errors.append(str(exc))
                        continue

                    if not issue_repo:
                        continue

                    new_items.extend(repo_items)
                    total_recent += repo_recent
                    total_new += repo_new
                    per_repo_counts[issue_repo] = repo_recent
                    previous_seen = int(state["last_seen"].get(issue_repo, 0) or 0)
                    state["last_seen"][issue_repo] = max(previous_seen, newest_epoch)
                    processed_repos += 1

                if partial:
                    for future in pending:
                        future.cancel()
                    break

        if repo_errors and not warning:
            warning = f"{len(repo_errors)} repo(s) failed; results may be incomplete."
        elif repo_errors and warning:
            warning = (
                f"{warning} {len(repo_errors)} repo(s) failed; results may be incomplete."
            )

        save_json(STATE_PATH, state)
        write_html_report(HTML_REPORT_PATH, new_items, days_back)

        new_items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        output: FetchOutput = {
            "total_recent": total_recent,
            "total_new": total_new,
            "per_repo_counts": per_repo_counts,
            "days_back": days_back,
            "max_display": int(cfg.get("max_display", 200)),
            "html_report_path": HTML_REPORT_PATH,
            "items": new_items,
            "processed_repos": processed_repos,
            "fetched_fork_repos": len(repos),
            "partial": partial,
            "authenticated": authenticated,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if warning:
            output["warning"] = warning
        if repo_errors:
            output["repo_errors"] = repo_errors[:5]
        print(json.dumps(output, indent=2))
    finally:
        session.close()


if __name__ == "__main__":
    main()
