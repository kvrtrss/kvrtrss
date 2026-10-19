#!/usr/bin/env python3
"""Generate light-mode, manga-panel style profile cards from the GitHub API.

Writes into ../assets:
  contributions.svg  - 12-month contribution grid
  stats.svg          - stars, commits, PRs, issues + a fun rank ring
  streak.svg         - total contributions, current streak, longest streak
  languages.svg      - top languages (not used by the README by default)

Standard library only. Set GH_TOKEN (the workflow does this). Only public data
is requested; extra queries fail soft so the cards still build.
"""
import html
import json
import math
import os
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

USER = os.environ.get("PROFILE_USER") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "kvrtrss"
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
OUT = Path(__file__).resolve().parent.parent / "assets"

# Light theme: BG is the panel colour, FG is ink/text.
BG, FG, DIM, SHADE = "#ffffff", "#000000", "#5c5c5c", "#c4c4c4"
MONO = "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace"

BASE_QUERY = """
query($login: String!) {
  user(login: $login) {
    followers { totalCount }
    repositories(privacy: PUBLIC, ownerAffiliations: OWNER, isFork: false, first: 100,
                 orderBy: {field: PUSHED_AT, direction: DESC}) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 8, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel } }
      }
    }
  }
}
"""

EXTRAS_QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    name
    createdAt
    pullRequests { totalCount }
    issues { totalCount }
    repositoriesContributedTo(first: 1, privacy: PUBLIC,
        contributionTypes: [COMMIT, ISSUE, PULL_REQUEST, REPOSITORY]) { totalCount }
    contributionsCollection { totalCommitContributions totalPullRequestReviewContributions }
  }
}
"""


def esc(s):
    return html.escape(str(s), quote=True)


BASE_CSS = (
    ".cu{animation:blink 1.1s steps(1) infinite}"
    "@keyframes blink{0%,50%{opacity:1}50.01%,100%{opacity:0}}"
)


def svg(w, h, body, title, css=""):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">'
        f"<title>{esc(title)}</title><style>{BASE_CSS}{css}</style>{body}</svg>"
    )


def panel(w, h, x=2, y=2, off=6):
    """A manga-style panel: white box, black border, offset grey shadow."""
    return (
        f'<rect x="{x + off}" y="{y + off}" width="{w - 10}" height="{h - 10}" fill="{SHADE}"/>'
        f'<rect x="{x}" y="{y}" width="{w - 10}" height="{h - 10}" fill="{BG}" '
        f'stroke="{FG}" stroke-width="2"/>'
    )


def label(text, w):
    return (
        f'<text x="24" y="36" fill="{FG}" font-family="{MONO}" font-size="13" '
        f'font-weight="700" letter-spacing="2.5">{esc(text)}</text>'
        f'<rect class="cu" x="{24 + len(text) * 10.3 + 2:.1f}" y="25" width="8" height="13" fill="{FG}"/>'
        f'<line x1="24" y1="46" x2="{w - 32}" y2="46" stroke="{DIM}" stroke-width="1"/>'
    )


# ---------------------------------------------------------------- data

class ApiError(Exception):
    pass


_OPENER = urllib.request.build_opener(type("_NoRedirect", (urllib.request.HTTPRedirectHandler,), {
    "redirect_request": lambda self, *a, **k: None,
}))


def gql(query, variables):
    if not TOKEN:
        sys.exit("Set GH_TOKEN (or GITHUB_TOKEN) to a GitHub token.")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={
            "Authorization": f"bearer {TOKEN}",
            "Content-Type": "application/json",
            "User-Agent": "profile-cards",
        },
    )
    try:
        with _OPENER.open(req, timeout=30) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise ApiError(f"HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise ApiError(f"unreachable: {e.reason}") from None
    if data.get("errors"):
        raise ApiError("; ".join(str(e.get("message", "error")) for e in data["errors"]))
    user = (data.get("data") or {}).get("user")
    if not user:
        raise ApiError("user not found")
    return user


def fetch_profile():
    try:
        return gql(BASE_QUERY, {"login": USER})
    except ApiError as e:
        sys.exit(f"GitHub API error: {e}")


def fetch_extras():
    """Nice-to-have numbers. Never raises: the cards still build without them."""
    try:
        return gql(EXTRAS_QUERY, {"login": USER})
    except ApiError as e:
        print(f"warning: extra stats unavailable ({e}); showing dashes", file=sys.stderr)
        return {}


def fetch_years(created, today):
    """Per-year contribution data, one aliased field per year. Soft-fails to None."""
    parts = []
    for y in range(max(created.year, today.year - 14), today.year + 1):
        to = "" if y == today.year else f', to: "{y}-12-31T23:59:59Z"'
        parts.append(
            f'y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z"{to}) {{ '
            "totalCommitContributions totalPullRequestReviewContributions "
            "contributionCalendar { weeks { contributionDays { date contributionCount } } } }"
        )
    query = "query($login: String!) { user(login: $login) { " + " ".join(parts) + " } }"
    try:
        return gql(query, {"login": USER})
    except ApiError as e:
        print(f"warning: lifetime history unavailable ({e}); using last 12 months", file=sys.stderr)
        return None


def merge_years(years, today):
    days, commits, reviews = {}, 0, 0
    for col in years.values():
        commits += col["totalCommitContributions"]
        reviews += col["totalPullRequestReviewContributions"]
        for w in col["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                dt = date.fromisoformat(d["date"])
                if dt <= today:
                    days[dt] = max(days.get(dt, 0), d["contributionCount"])
    return days, commits, reviews


def streaks(days):
    """days: [(date, count)] ascending, contiguous. Returns (current, longest), each (length, start, end)."""
    longest, run, run_start = (0, None, None), 0, None
    for dt, c in days:
        if c > 0:
            if run == 0:
                run_start = dt
            run += 1
            if run > longest[0]:
                longest = (run, run_start, dt)
        else:
            run = 0
    i = len(days) - 1
    if i >= 0 and days[i][1] == 0:  # today may simply not have activity yet
        i -= 1
    length, start, end = 0, None, None
    while i >= 0 and days[i][1] > 0:
        if length == 0:
            end = days[i][0]
        length += 1
        start = days[i][0]
        i -= 1
    return (length, start, end), longest


def rank_of(commits, prs, issues, reviews, stars, followers):
    """A simple, fun score (not an official GitHub metric). Returns (letter, percentile)."""
    exp_cdf = lambda x: 1 - 2 ** -x
    log_cdf = lambda x: x / (1 + x)
    parts = [
        (2, exp_cdf(commits / 250)), (3, exp_cdf(prs / 50)), (1, exp_cdf(issues / 25)),
        (1, exp_cdf(reviews / 2)), (4, log_cdf(stars / 50)), (1, log_cdf(followers / 10)),
    ]
    pct = 100 * (1 - sum(w * v for w, v in parts) / sum(w for w, _ in parts))
    for t, lvl in zip([1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100],
                      ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]):
        if pct <= t:
            return lvl, pct
    return "C", pct


def fmt_day(d, year=False):
    s = f"{d:%b} {d.day}".upper()
    return f"{s}, {d.year}" if year else s


def fmt_range(a, b, today):
    if a is None:
        return "NO ACTIVE STREAK"
    if a == b:
        return fmt_day(a, a.year != today.year)
    if a.year == b.year:
        return f"{fmt_day(a)} - {fmt_day(b, b.year != today.year)}"
    return f"{fmt_day(a, True)} - {fmt_day(b, True)}"


# ---------------------------------------------------------------- cards

LEVEL_FILL = {
    "NONE": (None, SHADE),
    "FIRST_QUARTILE": ("#b3b3b3", "#b3b3b3"),
    "SECOND_QUARTILE": ("#737373", "#737373"),
    "THIRD_QUARTILE": ("#3a3a3a", "#3a3a3a"),
    "FOURTH_QUARTILE": (FG, FG),
}


def contributions_card(cal):
    W, H = 800, 220
    weeks = cal["weeks"]
    n = len(weeks)
    pitch = min(14, (W - 10 - 48) / n)
    size = pitch - 3
    x0, y0 = 24, 74
    cells, months, last_month = [], [], None
    for wi, wk in enumerate(weeks):
        col = []
        for d in wk["contributionDays"]:
            dt = date.fromisoformat(d["date"])
            row = (dt.weekday() + 1) % 7  # Sunday = 0
            fill, stroke = LEVEL_FILL.get(d["contributionLevel"], LEVEL_FILL["NONE"])
            f = f'fill="{fill}"' if fill else 'fill="none"'
            col.append(
                f'<rect x="{x0 + wi * pitch:.1f}" y="{y0 + row * pitch:.1f}" '
                f'width="{size:.1f}" height="{size:.1f}" {f} stroke="{stroke}" stroke-width="1"/>'
            )
            if row == 0 and dt.month != last_month and dt.day <= 7:
                months.append(
                    f'<text x="{x0 + wi * pitch:.1f}" y="66" fill="{DIM}" font-family="{MONO}" '
                    f'font-size="10" letter-spacing="1">{dt.strftime("%b").upper()}</text>'
                )
                last_month = dt.month
        cells.append(f'<g class="w" style="animation-delay:{wi * 0.07:.2f}s">' + "".join(col) + "</g>")
    total = cal["totalContributions"]
    legend_x = W - 10 - 24 - 5 * 16 - 70
    legend = (
        f'<text x="{legend_x}" y="198" fill="{DIM}" font-family="{MONO}" font-size="10" '
        f'letter-spacing="1">LESS</text>'
    )
    for i, lvl in enumerate(LEVEL_FILL):
        fill, stroke = LEVEL_FILL[lvl]
        f = f'fill="{fill}"' if fill else 'fill="none"'
        legend += (
            f'<rect x="{legend_x + 36 + i * 16}" y="189" width="11" height="11" {f} '
            f'stroke="{stroke}" stroke-width="1"/>'
        )
    legend += (
        f'<text x="{legend_x + 36 + 5 * 16 + 4}" y="198" fill="{DIM}" font-family="{MONO}" '
        f'font-size="10" letter-spacing="1">MORE</text>'
    )
    body = (
        panel(W, H)
        + label("CONTRIBUTIONS / LAST 12 MONTHS", W)
        + f'<text x="{W - 32}" y="36" text-anchor="end" fill="{FG}" font-family="{MONO}" '
        f'font-size="13" font-weight="700">{total:,}</text>'
        + "".join(months) + "".join(cells) + legend
    )
    css = ".w{animation:scan 6s ease-in-out infinite}@keyframes scan{0%,30%,100%{opacity:1}12%{opacity:.25}}"
    return svg(W, H, body, f"{total} contributions in the last 12 months", css)


def icon(kind, x, y):
    """Tiny 14x14 line icons drawn from basic shapes."""
    st = f'fill="none" stroke="{FG}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"'
    if kind == "star":
        pts = " ".join(
            f"{7 + (7 if i % 2 == 0 else 3) * math.cos(-math.pi / 2 + i * math.pi / 5):.2f},"
            f"{7.5 + (7 if i % 2 == 0 else 3) * math.sin(-math.pi / 2 + i * math.pi / 5):.2f}"
            for i in range(10)
        )
        inner = f'<polygon points="{pts}"/>'
    elif kind == "commit":
        inner = '<circle cx="7" cy="7" r="6"/><polyline points="7,3.5 7,7 10,8.5"/>'
    elif kind == "pr":
        inner = ('<circle cx="3" cy="3" r="1.8"/><circle cx="3" cy="11" r="1.8"/>'
                 '<circle cx="11" cy="11" r="1.8"/><line x1="3" y1="4.8" x2="3" y2="9.2"/>'
                 '<polyline points="11,9.2 11,4 8,4"/>')
    elif kind == "issue":
        inner = ('<circle cx="7" cy="7" r="6"/><line x1="7" y1="4" x2="7" y2="7.5"/>'
                 f'<circle cx="7" cy="9.8" r="0.5" fill="{FG}"/>')
    else:  # repo
        inner = '<rect x="2" y="1" width="10" height="12" rx="1"/><line x1="2" y1="10" x2="12" y2="10"/>'
    return f'<g transform="translate({x},{y})" {st}>{inner}</g>'


def stats_card(name, rows, level, pct):
    W, H = 400, 230
    out = panel(W, H) + label(f"{name[:14].upper()}'S GITHUB STATS", W)
    for i, (kind, k, v) in enumerate(rows):
        y = 80 + i * 28
        out += (
            icon(kind, 24, y - 11)
            + f'<text x="46" y="{y}" fill="{DIM}" font-family="{MONO}" font-size="11" '
            f'letter-spacing="1">{esc(k)}</text>'
            f'<text x="246" y="{y}" text-anchor="end" fill="{FG}" font-family="{MONO}" '
            f'font-size="14" font-weight="700">{esc(v)}</text>'
        )
    cx, cy, r = 322, 140, 34
    circ = 2 * math.pi * r
    filled = circ * max(0.05, min(1, (100 - pct) / 100))
    out += (
        f'<circle cx="{cx}" cy="{cy}" r="{r + 12}" fill="none" stroke="{DIM}" stroke-width="0.6" stroke-dasharray="1 4"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{SHADE}" stroke-width="6"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{FG}" stroke-width="6" '
        f'stroke-linecap="round" stroke-dasharray="{filled:.1f} {circ:.1f}" transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy + 10}" text-anchor="middle" fill="{FG}" font-family="{MONO}" '
        f'font-size="28" font-weight="700">{esc(level)}</text>'
        f'<g class="orb"><circle cx="{cx}" cy="{cy - r - 12}" r="3" fill="{FG}"/></g>'
    )
    css = (f".orb{{transform-origin:{cx}px {cy}px;animation:orbit 10s linear infinite}}"
           "@keyframes orbit{to{transform:rotate(360deg)}}")
    return svg(W, H, out, "GitHub stats", css)


def streak_card(total, since, cur, longest, today):
    W, H = 400, 230
    out = panel(W, H)
    cy, r = 94, 36
    circ = 2 * math.pi * r
    gap = 46
    out += (
        f'<line x1="135" y1="56" x2="135" y2="186" stroke="{DIM}" stroke-width="1"/>'
        f'<line x1="265" y1="56" x2="265" y2="186" stroke="{DIM}" stroke-width="1"/>'
    )
    cols = [
        (80, f"{total:,}", "CONTRIBUTIONS", since),
        (200, str(cur[0]), "CURRENT STREAK", fmt_range(cur[1], cur[2], today)),
        (320, str(longest[0]), "LONGEST STREAK", fmt_range(longest[1], longest[2], today)),
    ]
    for x, num, lab, sub in cols:
        out += (
            f'<text x="{x}" y="{cy + 11}" text-anchor="middle" fill="{FG}" font-family="{MONO}" '
            f'font-size="30" font-weight="700">{esc(num)}</text>'
            f'<text x="{x}" y="162" text-anchor="middle" fill="{FG}" font-family="{MONO}" '
            f'font-size="10" font-weight="700" letter-spacing="1">{esc(lab)}</text>'
        )
        lines = sub.split(" - ", 1) if len(sub) > 18 else [sub]
        for j, ln in enumerate(lines):
            tail = " -" if j == 0 and len(lines) > 1 else ""
            out += (
                f'<text x="{x}" y="{178 + j * 12}" text-anchor="middle" fill="{DIM}" font-family="{MONO}" '
                f'font-size="9" letter-spacing="0.3">{esc(ln + tail)}</text>'
            )
    out += (
        f'<circle cx="200" cy="{cy}" r="{r}" fill="none" stroke="{FG}" stroke-width="4" stroke-linecap="round" '
        f'stroke-dasharray="{circ - gap:.1f} {gap:.1f}" transform="rotate({-90 + gap / circ * 180:.1f} 200 {cy})"/>'
        f'<g transform="translate(190,{cy - r - 14}) scale(1.25)" fill="{BG}" stroke="{FG}" stroke-width="1.5" '
        f'stroke-linejoin="round" stroke-linecap="round"><g class="fl">'
        f'<path d="M8 0 C9.5 4 14 6.5 14 12 A6 6 0 0 1 2 12 C2 9 3.6 7.4 4.6 5.6 C5 7.2 5.8 8 6.8 8 C6.6 5 7 2.4 8 0 Z"/>'
        f'<path fill="none" d="M8 17 C6.4 17 5.4 15.8 5.4 14.4 C5.4 13 6.6 12.2 7.2 11 C8 12 10.6 12.8 10.6 14.4 C10.6 15.8 9.6 17 8 17 Z"/>'
        f'</g></g>'
    )
    css = (".fl{transform-box:fill-box;transform-origin:50% 100%;animation:flick 1.8s ease-in-out infinite}"
           "@keyframes flick{0%,100%{transform:scale(1,1)}50%{transform:scale(1.08,1.14)}}")
    return svg(W, H, out, "Contribution streak", css)


PATTERNS = f"""
<defs>
  <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <rect width="6" height="6" fill="{BG}"/><line x1="0" y1="0" x2="0" y2="6" stroke="{FG}" stroke-width="2.5"/>
  </pattern>
  <pattern id="dots" width="6" height="6" patternUnits="userSpaceOnUse">
    <rect width="6" height="6" fill="{BG}"/><circle cx="3" cy="3" r="1.7" fill="{FG}"/>
  </pattern>
  <pattern id="cross" width="6" height="6" patternUnits="userSpaceOnUse">
    <rect width="6" height="6" fill="{BG}"/>
    <line x1="0" y1="3" x2="6" y2="3" stroke="{FG}" stroke-width="1.5"/>
    <line x1="3" y1="0" x2="3" y2="6" stroke="{FG}" stroke-width="1.5"/>
  </pattern>
</defs>"""
BAR_FILLS = [FG, "url(#hatch)", "url(#dots)", "url(#cross)", DIM]


def languages_card(langs):
    W, H = 400, 230
    out = PATTERNS + panel(W, H) + label("TOP LANGUAGES", W)
    total = sum(s for _, s in langs) or 1
    top = langs[:5]
    if not top:
        out += (f'<text x="24" y="100" fill="{DIM}" font-family="{MONO}" font-size="13">'
                "NO CODE DETECTED YET</text>")
    biggest = top[0][1] if top else 1
    for i, (name, size) in enumerate(top):
        y = 80 + i * 28
        pct = size / total * 100
        w = max(6, size / biggest * 185)
        out += (
            f'<text x="24" y="{y}" fill="{FG}" font-family="{MONO}" font-size="13">{esc(name[:12])}</text>'
            f'<rect class="bar" style="animation-delay:{0.2 + i * 0.15:.2f}s" x="124" y="{y - 12}" '
            f'width="{w:.0f}" height="14" fill="{BAR_FILLS[i]}" stroke="{FG}" stroke-width="1.5"/>'
            f'<text x="{W - 32}" y="{y}" text-anchor="end" fill="{DIM}" font-family="{MONO}" '
            f'font-size="12">{pct:.1f}%</text>'
        )
    css = (".bar{transform-box:fill-box;transform-origin:0 50%;animation:grow .9s ease-out backwards}"
           "@keyframes grow{from{transform:scaleX(0)}}")
    return svg(W, H, out, "Top languages", css)


# ---------------------------------------------------------------- demo

def _level(c):
    return ("NONE" if c == 0 else "FIRST_QUARTILE" if c < 2 else "SECOND_QUARTILE" if c < 4
            else "THIRD_QUARTILE" if c < 7 else "FOURTH_QUARTILE")


def demo_data(today):
    """Fake data, only used with --demo to preview the layout."""
    import random
    rng = random.Random(4)
    start = today - timedelta(days=500)
    days, d = {}, start
    while d <= today:
        days[d] = rng.choice([0, 0, 0, 1, 2, 4, 7]) if d > start + timedelta(days=60) else 0
        d += timedelta(days=1)
    for i in range(6):
        days[today - timedelta(days=i)] = rng.choice([1, 2, 4, 7])
    s = today - timedelta(days=364)
    s -= timedelta(days=(s.weekday() + 1) % 7)
    weeks, d = [], s
    while d <= today:
        wk = []
        for i in range(7):
            dd = d + timedelta(days=i)
            if dd <= today:
                wk.append({"date": dd.isoformat(), "contributionCount": days[dd],
                           "contributionLevel": _level(days[dd])})
        weeks.append({"contributionDays": wk})
        d += timedelta(days=7)
    langs = [("C#", 52000), ("Python", 31000), ("JavaScript", 14000), ("HTML", 6000), ("ShaderLab", 2500)]
    user = {
        "followers": {"totalCount": 3},
        "repositories": {"totalCount": 3, "nodes": [{"stargazerCount": 2, "languages": {"edges": [
            {"size": sz, "node": {"name": n}} for n, sz in langs]}}]},
        "contributionsCollection": {"contributionCalendar": {
            "totalContributions": sum(c["contributionCount"] for w in weeks for c in w["contributionDays"]),
            "weeks": weeks}},
    }
    extras = {"name": "kvrtrss", "createdAt": (today - timedelta(days=450)).isoformat() + "T00:00:00Z",
              "pullRequests": {"totalCount": 3}, "issues": {"totalCount": 1},
              "repositoriesContributedTo": {"totalCount": 0},
              "contributionsCollection": {"totalCommitContributions": 120,
                                          "totalPullRequestReviewContributions": 0}}
    years = {}
    for y in sorted({k.year for k in days}):
        ds = [{"date": k.isoformat(), "contributionCount": v} for k, v in sorted(days.items()) if k.year == y]
        years[f"y{y}"] = {"totalCommitContributions": 60, "totalPullRequestReviewContributions": 0,
                          "contributionCalendar": {"weeks": [{"contributionDays": ds}]}}
    return user, extras, years


# ---------------------------------------------------------------- main

def main():
    demo = "--demo" in sys.argv
    out_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT
    out_dir.mkdir(parents=True, exist_ok=True)
    today = date.today()

    if demo:
        user, extras, years = demo_data(today)
    else:
        user = fetch_profile()
        extras = fetch_extras()
        years = None
        if extras.get("createdAt"):
            years = fetch_years(date.fromisoformat(extras["createdAt"][:10]), today)

    cal = user["contributionsCollection"]["contributionCalendar"]
    col12 = extras.get("contributionsCollection") or {}
    if years:
        days_map, commits, reviews = merge_years(years, today)
        total = sum(days_map.values())
        since = "SINCE " + fmt_day(date.fromisoformat(extras["createdAt"][:10]), True)
        commits_label = "TOTAL COMMITS"
    else:
        days_map = {date.fromisoformat(d["date"]): d["contributionCount"]
                    for w in cal["weeks"] for d in w["contributionDays"]
                    if date.fromisoformat(d["date"]) <= today}
        total = cal["totalContributions"]
        since = "LAST 12 MONTHS"
        commits = col12.get("totalCommitContributions")
        reviews = col12.get("totalPullRequestReviewContributions") or 0
        commits_label = "COMMITS (12 MO)"
    cur, longest = streaks(sorted(days_map.items()))

    repos = user["repositories"]["nodes"]
    stars = sum(r["stargazerCount"] for r in repos)
    followers = user["followers"]["totalCount"]
    sizes = defaultdict(int)
    for r in repos:
        for e in r["languages"]["edges"]:
            sizes[e["node"]["name"]] += e["size"]
    langs = sorted(sizes.items(), key=lambda kv: -kv[1])

    prs = (extras.get("pullRequests") or {}).get("totalCount")
    issues = (extras.get("issues") or {}).get("totalCount")
    contrib_to = (extras.get("repositoriesContributedTo") or {}).get("totalCount")
    level, pct = rank_of(commits or 0, prs or 0, issues or 0, reviews or 0, stars, followers)
    num = lambda v: "-" if v is None else f"{v:,}"
    rows = [
        ("star", "TOTAL STARS", num(stars)),
        ("commit", commits_label, num(commits)),
        ("pr", "TOTAL PRS", num(prs)),
        ("issue", "TOTAL ISSUES", num(issues)),
        ("repo", "CONTRIBUTED TO", num(contrib_to)),
    ]
    name = extras.get("name") or extras.get("login") or USER

    (out_dir / "contributions.svg").write_text(contributions_card(cal), encoding="utf-8")
    (out_dir / "stats.svg").write_text(stats_card(name, rows, level, pct), encoding="utf-8")
    (out_dir / "streak.svg").write_text(streak_card(total, since, cur, longest, today), encoding="utf-8")
    (out_dir / "languages.svg").write_text(languages_card(langs), encoding="utf-8")
    print(f"Wrote cards for {USER} to {out_dir}")


if __name__ == "__main__":
    main()
