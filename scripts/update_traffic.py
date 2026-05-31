from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOS_FILE = ROOT / ".github" / "profile-traffic-repos.txt"
OUT_DIR = ROOT / "assets" / "traffic"
HISTORY_FILE = OUT_DIR / "history.json"
MAX_HISTORY_DAYS = 370


def load_repos() -> list[str]:
    return [
        line.strip()
        for line in REPOS_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def github_get(path: str, token: str) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "Wezylnia-profile-traffic",
        },
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def load_history() -> dict:
    if not HISTORY_FILE.exists():
        return {}

    return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))


def save_history(history: dict) -> None:
    cutoff = date.today() - timedelta(days=MAX_HISTORY_DAYS)

    for repo, days in list(history.items()):
        history[repo] = {
            day: values
            for day, values in sorted(days.items())
            if date.fromisoformat(day) >= cutoff
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def merge_views(history: dict, repo: str, payload: dict) -> None:
    repo_history = history.setdefault(repo, {})

    for item in payload.get("views", []):
        day = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")).date().isoformat()
        repo_history[day] = {
            "count": int(item.get("count", 0)),
            "uniques": int(item.get("uniques", 0)),
        }


def series_for(history: dict, repo: str, days: int) -> list[tuple[str, int, int]]:
    today = datetime.now(timezone.utc).date()
    repo_history = history.get(repo, {})
    series = []

    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        values = repo_history.get(day.isoformat(), {})
        series.append((day.isoformat(), int(values.get("count", 0)), int(values.get("uniques", 0))))

    return series


def render_svg(repo: str, title: str, series: list[tuple[str, int, int]]) -> str:
    width = 720
    height = 220
    padding_left = 48
    padding_right = 24
    padding_top = 42
    padding_bottom = 42
    chart_width = width - padding_left - padding_right
    chart_height = height - padding_top - padding_bottom
    max_value = max([count for _, count, _ in series] + [1])
    total = sum(count for _, count, _ in series)
    uniques = sum(unique for _, _, unique in series)
    bar_gap = 4
    bar_width = max(3, (chart_width - (len(series) - 1) * bar_gap) / len(series))
    bars = []

    for index, (day, count, _) in enumerate(series):
        bar_height = 0 if count == 0 else max(2, chart_height * count / max_value)
        x = padding_left + index * (bar_width + bar_gap)
        y = padding_top + chart_height - bar_height
        label = datetime.fromisoformat(day).strftime("%b %d")
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{bar_width:.2f}" height="{bar_height:.2f}" '
            f'rx="2" fill="#0e75b6"><title>{escape(label)}: {count} views</title></rect>'
        )

    last_day = datetime.fromisoformat(series[-1][0]).strftime("%b %d") if series else ""
    first_day = datetime.fromisoformat(series[0][0]).strftime("%b %d") if series else ""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(repo)} {escape(title)} traffic chart">
  <rect width="{width}" height="{height}" fill="#ffffff"/>
  <text x="24" y="28" fill="#1f2937" font-family="Segoe UI, Arial, sans-serif" font-size="18" font-weight="600">{escape(title)}</text>
  <text x="{width - 24}" y="28" text-anchor="end" fill="#4b5563" font-family="Segoe UI, Arial, sans-serif" font-size="13">{total} views · {uniques} unique</text>
  <line x1="{padding_left}" y1="{padding_top + chart_height}" x2="{width - padding_right}" y2="{padding_top + chart_height}" stroke="#d1d5db"/>
  <line x1="{padding_left}" y1="{padding_top}" x2="{padding_left}" y2="{padding_top + chart_height}" stroke="#d1d5db"/>
  {''.join(bars)}
  <text x="{padding_left}" y="{height - 16}" fill="#6b7280" font-family="Segoe UI, Arial, sans-serif" font-size="12">{escape(first_day)}</text>
  <text x="{width - padding_right}" y="{height - 16}" text-anchor="end" fill="#6b7280" font-family="Segoe UI, Arial, sans-serif" font-size="12">{escape(last_day)}</text>
</svg>
"""


def slug(repo: str) -> str:
    return repo.split("/", 1)[1].lower().replace("_", "-")


def write_chart_files(history: dict, repos: list[str]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for repo in repos:
        repo_name = repo.split("/", 1)[1]
        repo_slug = slug(repo)
        weekly = series_for(history, repo, 7)
        monthly = series_for(history, repo, 30)
        (OUT_DIR / f"{repo_slug}-week.svg").write_text(
            render_svg(repo_name, "Last 7 days", weekly), encoding="utf-8"
        )
        (OUT_DIR / f"{repo_slug}-month.svg").write_text(
            render_svg(repo_name, "Last 30 days", monthly), encoding="utf-8"
        )


def main() -> int:
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 1

    repos = load_repos()
    history = load_history()

    for repo in repos:
        try:
            payload = github_get(f"/repos/{repo}/traffic/views?per=day", token)
            merge_views(history, repo, payload)
            print(f"Updated traffic data for {repo}")
        except urllib.error.HTTPError as error:
            print(f"Skipping {repo}: GitHub API returned {error.code}", file=sys.stderr)

    save_history(history)
    write_chart_files(history, repos)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
