#!/usr/bin/env python3
"""Generate monochrome, manga-panel style profile cards from the GitHub API.

Writes three SVGs into ../assets:
  contributions.svg  - 12-month contribution grid
  stats.svg          - streaks, repos, stars, followers
  languages.svg      - top languages, drawn with screentone patterns

Needs only the standard library. Set GH_TOKEN (the workflow does this).
"""
import html
import json
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

INK, PAPER, DIM, SHADE = "#000000", "#ffffff", "#8c8c8c", "#3a3a3a"
MONO = "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace"

QUERY = """
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


def esc(s):
    return html.escape(str(s), quote=True)


REDUCED = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
BASE_CSS = (
    ".cu{animation:blink 1.1s steps(1) infinite}"
    "@keyframes blink{0%,50%{opacity:1}50.01%,100%{opacity:0}}"
)


def svg(w, h, body, title, css=""):
    # css is built only from constants in this file, never from API data.
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">'
        f"<title>{esc(title)}</title><style>{BASE_CSS}{css}{REDUCED}</style>{body}</svg>"
    )


def panel(w, h, x=2, y=2, off=6):
    """A manga-style panel: black box, white border, offset grey shadow."""
    return (
        f'<rect x="{x + off}" y="{y + off}" width="{w - 10}" height="{h - 10}" fill="{SHADE}"/>'
        f'<rect x="{x}" y="{y}" width="{w - 10}" height="{h - 10}" fill="{INK}" '
        f'stroke="{PAPER}" stroke-width="2"/>'
    )


def label(text, w):
    return (
        f'<text x="24" y="36" fill="{PAPER}" font-family="{MONO}" font-size="13" '
        f'font-weight="700" letter-spacing="2.5">{esc(text)}</text>'
        f'<rect class="cu" x="{24 + len(text) * 10.3 + 2:.1f}" y="25" width="8" height="13" fill="{PAPER}"/>'
        f'<line x1="24" y1="46" x2="{w - 32}" y2="46" stroke="{DIM}" stroke-width="1"/>'
    )


# ---------------------------------------------------------------- data

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects so the Authorization header can never be forwarded elsewhere."""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def fetch():
    if not TOKEN:
        sys.exit("Set GH_TOKEN (or GITHUB_TOKEN) to a GitHub token.")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": USER}}).encode(),
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
        sys.exit(f"GitHub API request failed: HTTP {e.code}")
    except urllib.error.URLError as e:
        sys.exit(f"GitHub API unreachable: {e.reason}")
    if data.get("errors"):
        sys.exit(json.dumps(data["errors"], indent=2))
    return data["data"]["user"]


def streaks(days):
    """days: [(date, count)] sorted ascending. Returns (current, longest)."""
    longest = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    i = len(days) - 1
    if i >= 0 and days[i][1] == 0:  # today may simply not have activity yet
        i -= 1
    current = 0
    while i >= 0 and days[i][1] > 0:
        current += 1
        i -= 1
    return current, longest


# ---------------------------------------------------------------- cards

LEVEL_FILL = {
    "NONE": (None, SHADE),
    "FIRST_QUARTILE": ("#4d4d4d", "#4d4d4d"),
    "SECOND_QUARTILE": ("#8c8c8c", "#8c8c8c"),
    "THIRD_QUARTILE": ("#c8c8c8", "#c8c8c8"),
    "FOURTH_QUARTILE": (PAPER, PAPER),
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
        + f'<text x="{W - 32}" y="36" text-anchor="end" fill="{PAPER}" font-family="{MONO}" '
        f'font-size="13" font-weight="700">{total:,}</text>'
        + "".join(months)
        + "".join(cells)
        + legend
    )
    css = ".w{animation:scan 6s ease-in-out infinite}@keyframes scan{0%,30%,100%{opacity:1}12%{opacity:.25}}"
    return svg(W, H, body, f"{total} contributions in the last 12 months", css)


def stats_card(rows):
    W, H = 400, 230
    out = panel(W, H) + label("STATS", W)
    for i, (k, v) in enumerate(rows):
        y = 78 + i * 26
        out += (
            f'<text x="24" y="{y}" fill="{DIM}" font-family="{MONO}" font-size="12" '
            f'letter-spacing="1">{esc(k)}</text>'
            f'<text x="{W - 32}" y="{y}" text-anchor="end" fill="{PAPER}" font-family="{MONO}" '
            f'font-size="15" font-weight="700">{esc(v)}</text>'
        )
    return svg(W, H, out, "GitHub stats")


PATTERNS = f"""
<defs>
  <pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
    <rect width="6" height="6" fill="{INK}"/><line x1="0" y1="0" x2="0" y2="6" stroke="{PAPER}" stroke-width="2.5"/>
  </pattern>
  <pattern id="dots" width="6" height="6" patternUnits="userSpaceOnUse">
    <rect width="6" height="6" fill="{INK}"/><circle cx="3" cy="3" r="1.7" fill="{PAPER}"/>
  </pattern>
  <pattern id="cross" width="6" height="6" patternUnits="userSpaceOnUse">
    <rect width="6" height="6" fill="{INK}"/>
    <line x1="0" y1="3" x2="6" y2="3" stroke="{PAPER}" stroke-width="1.5"/>
    <line x1="3" y1="0" x2="3" y2="6" stroke="{PAPER}" stroke-width="1.5"/>
  </pattern>
</defs>"""
BAR_FILLS = [PAPER, "url(#hatch)", "url(#dots)", "url(#cross)", DIM]


def languages_card(langs):
    W, H = 400, 230
    out = PATTERNS + panel(W, H) + label("TOP LANGUAGES", W)
    total = sum(s for _, s in langs) or 1
    top = langs[:5]
    if not top:
        out += (
            f'<text x="24" y="100" fill="{DIM}" font-family="{MONO}" font-size="13">'
            f"NO CODE DETECTED YET</text>"
        )
    biggest = top[0][1] if top else 1
    for i, (name, size) in enumerate(top):
        y = 80 + i * 28
        pct = size / total * 100
        w = max(6, size / biggest * 185)
        out += (
            f'<text x="24" y="{y}" fill="{PAPER}" font-family="{MONO}" font-size="13">'
            f"{esc(name[:12])}</text>"
            f'<rect class="bar" style="animation-delay:{0.2 + i * 0.15:.2f}s" x="124" y="{y - 12}" width="{w:.0f}" height="14" fill="{BAR_FILLS[i]}" '
            f'stroke="{PAPER}" stroke-width="1.5"/>'
            f'<text x="{W - 32}" y="{y}" text-anchor="end" fill="{DIM}" font-family="{MONO}" '
            f'font-size="12">{pct:.1f}%</text>'
        )
    css = ".bar{transform-box:fill-box;transform-origin:0 50%;animation:grow .9s ease-out backwards}@keyframes grow{from{transform:scaleX(0)}}"
    return svg(W, H, out, "Top languages", css)


# ---------------------------------------------------------------- main

def demo_user():
    """Fake data, only used with --demo to preview the layout."""
    import random

    random.seed(4)
    levels = ["NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"]
    weeks, d0 = [], date(2025, 10, 19)
    for w in range(53):
        days = []
        for i in range(7):
            c = random.choice([0, 0, 0, 1, 2, 4, 7]) if w > 12 else 0
            lvl = levels[0 if c == 0 else 1 if c < 2 else 2 if c < 4 else 3 if c < 7 else 4]
            days.append({"date": (d0 + timedelta(days=w * 7 + i)).isoformat(),
                         "contributionCount": c, "contributionLevel": lvl})
        weeks.append({"contributionDays": days})
    langs = [("C#", 52000), ("Python", 31000), ("JavaScript", 14000), ("HTML", 6000), ("ShaderLab", 2500)]
    return {
        "followers": {"totalCount": 3},
        "repositories": {"totalCount": 3, "nodes": [
            {"stargazerCount": 2, "languages": {"edges": [
                {"size": s, "node": {"name": n}} for n, s in langs]}}]},
        "contributionsCollection": {"contributionCalendar": {"totalContributions": 412, "weeks": weeks}},
    }


def main():
    demo = "--demo" in sys.argv
    user = demo_user() if demo else fetch()
    out_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    cal = user["contributionsCollection"]["contributionCalendar"]
    today = date.today()
    days = sorted(
        (date.fromisoformat(d["date"]), d["contributionCount"])
        for w in cal["weeks"] for d in w["contributionDays"]
        if demo or date.fromisoformat(d["date"]) <= today
    )
    current, longest = streaks(days)

    repos = user["repositories"]["nodes"]
    stars = sum(r["stargazerCount"] for r in repos)
    sizes = defaultdict(int)
    for r in repos:
        for e in r["languages"]["edges"]:
            sizes[e["node"]["name"]] += e["size"]
    langs = sorted(sizes.items(), key=lambda kv: -kv[1])

    rows = [
        ("CONTRIBUTIONS / YR", f'{cal["totalContributions"]:,}'),
        ("CURRENT STREAK", f"{current} day{'' if current == 1 else 's'}"),
        ("LONGEST STREAK", f"{longest} day{'' if longest == 1 else 's'}"),
        ("PUBLIC REPOS", str(user["repositories"]["totalCount"])),
        ("STARS EARNED", str(stars)),
        ("FOLLOWERS", str(user["followers"]["totalCount"])),
    ]
    (out_dir / "contributions.svg").write_text(contributions_card(cal), encoding="utf-8")
    (out_dir / "stats.svg").write_text(stats_card(rows), encoding="utf-8")
    (out_dir / "languages.svg").write_text(languages_card(langs), encoding="utf-8")
    print(f"Wrote cards for {USER} to {out_dir}")


if __name__ == "__main__":
    main()
