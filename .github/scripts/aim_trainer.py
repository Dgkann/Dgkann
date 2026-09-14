"""Aim trainer widget for the profile README.

A crosshair clears every active day of the last year's contribution graph: human-like flicks with the odd miss,
a 25-round magazine, damage numbers, multi-kill/ACE callouts, a kill feed naming each day's repository, a radar,
and a sniper shot on the busiest day. Everything is SMIL, so it animates inside a README <img> and loops forever.

usage: python aim_trainer.py LOGIN OUT_SVG [--calendar FILE] [--repos FILE]
Without the files it asks the GitHub GraphQL API, using GITHUB_TOKEN (or GH_TOKEN).
Private repositories are never named; their days show up as "private".
"""

import argparse
import json
import math
import os
import random
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

W, H = 1200, 390
P, S = 20, 15            # cell pitch and size
X0, Y0 = 70, 74          # top-left of the contribution grid
WEEKS, DAYS = 53, 7
TEAL, PINK = "#00f5c4", "#ff2e88"
LEVELS = ["#0d2b24", "#0b4f42", "#0e8a70", "#12bf9b", TEAL]
MONO = "'Cascadia Code', 'Fira Code', 'JetBrains Mono', Consolas, 'Courier New', monospace"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
TZ = timedelta(hours=3)  # PRs, reviews and issues carry real timestamps; commit days are already whole dates

CALENDAR = """query($login: String!) { user(login: $login) { contributionsCollection { contributionCalendar {
  totalContributions weeks { contributionDays { date contributionCount } } } } } }"""

WORK = """query($login: String!) { user(login: $login) { contributionsCollection {
  commitContributionsByRepository(maxRepositories: 100) { repository { name isPrivate }
    contributions(first: 100) { nodes { occurredAt commitCount } } }
  pullRequestContributions(first: 100) { nodes { occurredAt pullRequest { repository { name isPrivate } } } }
  pullRequestReviewContributions(first: 100) { nodes { occurredAt pullRequest { repository { name isPrivate } } } }
  issueContributions(first: 100) { nodes { occurredAt issue { repository { name isPrivate } } } }
  repositoryContributions(first: 100) { nodes { occurredAt repository { name isPrivate } } }
} } }"""

_uid = [0]


# ------------------------------------------------------------ data

def graphql(query, login):
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("set GITHUB_TOKEN (or GH_TOKEN), or pass --calendar and --repos")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"bearer {token}", "Content-Type": "application/json", "User-Agent": "aim_trainer.py"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    if data.get("errors"):
        raise SystemExit(f"GraphQL error: {data['errors']}")
    return data


def load_grid(calendar):
    cal = calendar["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    weeks = [[(date.fromisoformat(d["date"]), d["contributionCount"]) for d in w["contributionDays"]] for w in cal["weeks"]]
    raw = [(wi, (day.weekday() + 1) % 7, count, day) for wi, week in enumerate(weeks) for day, count in week]
    nonzero = sorted(c for _, _, c, _ in raw if c)
    q = [nonzero[int(len(nonzero) * f)] for f in (.25, .5, .75)] if nonzero else [1, 1, 1]
    cells = [(w, d, c, 0 if c == 0 else 1 + sum(c > t for t in q), day) for w, d, c, day in raw]
    return cal["totalContributions"], weeks, cells


def repo_days(work):
    """Maps each day to the repository that got the most of that day's work."""
    c = work["data"]["user"]["contributionsCollection"]
    per_day = defaultdict(lambda: defaultdict(int))

    def add(day, repo, n):
        per_day[day]["private" if repo["isPrivate"] else repo["name"]] += n

    def local_day(stamp):
        return (datetime.fromisoformat(stamp.replace("Z", "+00:00")) + TZ).date().isoformat()

    for r in c["commitContributionsByRepository"]:
        for n in r["contributions"]["nodes"]:
            add(n["occurredAt"][:10], r["repository"], n["commitCount"])
    for key, field in (("pullRequestContributions", "pullRequest"), ("pullRequestReviewContributions", "pullRequest"),
                       ("issueContributions", "issue")):
        for n in c[key]["nodes"]:
            add(local_day(n["occurredAt"]), n[field]["repository"], 1)
    for n in c["repositoryContributions"]["nodes"]:
        add(local_day(n["occurredAt"]), n["repository"], 1)
    return {day: max(repos.items(), key=lambda kv: kv[1])[0] for day, repos in sorted(per_day.items())}


# ------------------------------------------------------------ svg helpers

def kt(t, T):
    return f"{max(0.0, min(1.0, t / T)):.5f}"


def discrete(attr, pairs, T):
    """pairs: [(time, value)] starting at 0; each value holds until the next time."""
    return (f'<animate attributeName="{attr}" calcMode="discrete" values="{";".join(str(v) for _, v in pairs)}" '
            f'keyTimes="{";".join(kt(t, T) for t, _ in pairs)}" dur="{T:.3f}s" repeatCount="indefinite"/>')


def linear(attr, pairs, T):
    """pairs: [(time, value)] covering 0..T, interpolated linearly."""
    return (f'<animate attributeName="{attr}" values="{";".join(str(v) for _, v in pairs)}" '
            f'keyTimes="{";".join(kt(t, T) for t, _ in pairs)}" dur="{T:.3f}s" repeatCount="indefinite"/>')


def moves(pairs, T):
    """pairs: [(time, x, y)] covering 0..T; linear translate between them."""
    return ('<animateTransform attributeName="transform" type="translate" '
            f'values="{";".join(f"{x:.1f} {y:.1f}" for _, x, y in pairs)}" '
            f'keyTimes="{";".join(kt(t, T) for t, _, _ in pairs)}" dur="{T:.3f}s" repeatCount="indefinite"/>')


def scale_anim(pairs, T):
    return ('<animateTransform attributeName="transform" type="scale" '
            f'values="{";".join(str(v) for _, v in pairs)}" keyTimes="{";".join(kt(t, T) for t, _ in pairs)}" '
            f'dur="{T:.3f}s" repeatCount="indefinite"/>')


def windows(spans, T):
    pairs = [(0, 0)]
    for a, b in spans:
        pairs += [(a, 1), (b, 0)]
    return discrete("opacity", pairs, T)


def counter(x, y, times, T, labels, cls="txt", line=26, width=90):
    """Odometer text: labels[k] is shown after k of the given times have passed."""
    _uid[0] += 1
    cid = f"cnt{_uid[0]}"
    texts = "".join(f'<text x="{x}" y="{y + k * line}" class="{cls}">{label}</text>' for k, label in enumerate(labels))
    pairs = [(0, 0)] + [(t, -k * line) for k, t in enumerate(sorted(times), 1)]
    anim = ('<animateTransform attributeName="transform" type="translate" calcMode="discrete" '
            f'values="{";".join(f"0 {v}" for _, v in pairs)}" keyTimes="{";".join(kt(t, T) for t, _ in pairs)}" '
            f'dur="{T:.3f}s" repeatCount="indefinite"/>')
    return (f'<clipPath id="{cid}"><rect x="{x - 4}" y="{y - line + 7}" width="{width}" height="{line}"/></clipPath>'
            f'<g clip-path="url(#{cid})"><g>{anim}{texts}</g></g>')


def center(w, d):
    return X0 + w * P + S / 2, Y0 + d * P + S / 2


def cell_rect(w, d, fill, opacity=1, inner=""):
    return f'<rect x="{X0 + w * P}" y="{Y0 + d * P}" width="{S}" height="{S}" rx="3" fill="{fill}" opacity="{opacity}">{inner}</rect>'


def dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def burst_slots(shots, T, slots=3, color=TEAL):
    """Replays a hit burst at every (time, x, y) in shots, cycling through a few reusable groups."""
    out = []
    for j in range(slots):
        mine = shots[j::slots]
        if not mine:
            continue
        pos = [(0, mine[0][1], mine[0][2])] + list(mine)
        pos_anim = ('<animateTransform attributeName="transform" type="translate" calcMode="discrete" '
                    f'values="{";".join(f"{x:.1f} {y:.1f}" for _, x, y in pos)}" '
                    f'keyTimes="{";".join(kt(t, T) for t, _, _ in pos)}" dur="{T:.3f}s" repeatCount="indefinite"/>')
        op, ring = [(0, 0)], [(0, 2)]
        for t, _, _ in mine:
            op += [(t - .001, 0), (t, 1), (t + .25, 0)]
            ring += [(t, 2), (t + .25, 20)]
        op.append((T, 0))
        ring.append((T, 20))
        parts = []
        for k in range(6):
            a = k * math.pi / 3 + .3
            dx, dy = 26 * math.cos(a), 26 * math.sin(a)
            tr = [(0, 0, 0)]
            for t, _, _ in mine:
                tr += [(t, 0, 0), (t + .25, dx, dy)]
            tr.append((T, dx, dy))
            parts.append(f'<rect x="-2.5" y="-2.5" width="5" height="5" fill="{color if k % 2 else "#eafffa"}">{moves(tr, T)}</rect>')
        out.append(f'<g>{pos_anim}<g>{linear("opacity", op, T)}{"".join(parts)}'
                   f'<circle r="2" fill="none" stroke="#eafffa" stroke-width="2">{linear("r", ring, T)}</circle></g></g>')
    return "".join(out)


def frame(defs, body, label):
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(label)}">
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#030607"/>
    <stop offset="1" stop-color="#062019"/>
  </linearGradient>
  <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
    <feGaussianBlur stdDeviation="3" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <clipPath id="frame"><rect width="{W}" height="{H}" rx="16"/></clipPath>
  {defs}
</defs>
<style>
  text {{ font-family: {MONO}; }}
  .dim {{ fill: #5f8f84; font-size: 15px; letter-spacing: 1px; }}
  .lbl {{ fill: #8fbfb3; font-size: 17px; }}
  .txt {{ fill: #c9fff3; font-size: 17px; }}
</style>
<g clip-path="url(#frame)">
  <rect width="{W}" height="{H}" fill="url(#bg)"/>
  {body}
</g>
<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="16" fill="none" stroke="{TEAL}" stroke-opacity=".35"/>
</svg>
"""


def title_bar(left, right):
    return (f'<rect width="{W}" height="48" fill="{TEAL}" fill-opacity=".05"/>'
            f'<line x1="0" y1="48.5" x2="{W}" y2="48.5" stroke="{TEAL}" stroke-opacity=".2"/>'
            f'<circle cx="30" cy="24" r="6" fill="{TEAL}">'
            f'<animate attributeName="opacity" values="1;.25;1" dur="2s" repeatCount="indefinite"/></circle>'
            f'<text x="48" y="30" class="lbl">{escape(left)}</text>'
            f'<text x="{W - 28}" y="30" class="dim" text-anchor="end">{escape(right)}</text>')


# ------------------------------------------------------------ widget

def weighted_greedy(points, start):
    """Nearest-neighbour order that prefers staying in the same week column, so streaks happen."""
    left, order, cur = list(points), [], start
    while left:
        nxt = min(left, key=lambda p: ((p[0] - cur[0]) * 1.6) ** 2 + (p[1] - cur[1]) ** 2)
        left.remove(nxt)
        order.append(nxt)
        cur = nxt
    return order


def short(name, limit=24):
    """Cuts long repository names at a word boundary so kill feed lines stay short."""
    if len(name) <= limit:
        return name
    cut = name[:limit - 1]
    for sep in "-_. ":
        if sep in cut:
            cut = cut[:cut.rindex(sep)] if cut.rindex(sep) >= 8 else cut
            break
    return cut + "…"


def aim_svg(total, weeks, cells, repos, login):
    rng = random.Random(21)
    active = [c for c in cells if c[2]]
    info = {(w, d): (c, lvl, day) for w, d, c, lvl, day in active}
    week_active = Counter(w for w, *_ in active)
    mvp = max(active, key=lambda c: (c[2], c[0]))
    mvp_key = (mvp[0], mvp[1])
    home = (X0 + 26 * P + S / 2, Y0 + 3 * P + S / 2)
    order = weighted_greedy([(*center(w, d), (w, d)) for w, d, *_ in active if (w, d) != mvp_key], home)
    hp = {1: 1, 2: 1, 3: 2, 4: 3}
    tap, reload_time, mag = .11, .8, 25

    t = 1.0
    path = [(0, *home), (t, *home)]
    prev = home
    ammo, fired, landed = mag, 0, 0
    hits_all, misses, rifle_shots, kills, headshots, reloads, hits_by = [], [], [], [], [], [], {}
    ammo_times, ammo_labels = [], [str(mag)]
    acc_times, acc_labels = [], ["100%"]

    for x, y, key in order:
        c, lvl, day = info[key]
        gap = dist(prev, (x, y))
        flick = min(.22, max(.07, .07 + gap * .0005))
        if path[-1][0] < t:
            path.append((t, *prev))
        if gap > 30:  # overshoot a little, then correct
            over = min(10, gap * .06)
            path.append((t + flick * .8, x + (x - prev[0]) / gap * over, y + (y - prev[1]) / gap * over))
            t += flick + .04
        else:
            t += flick
        path.append((t, x, y))

        hits, s = [], 0
        while s < hp[lvl]:
            if ammo == 0:
                reloads.append((t, t + reload_time))
                t += reload_time
                ammo = mag
                ammo_times.append(t)
                ammo_labels.append(str(mag))
            t += rng.uniform(.04, .07) if s == 0 else tap
            st = t
            ammo -= 1
            fired += 1
            ammo_times.append(st)
            ammo_labels.append(str(ammo))
            rifle_shots.append(st)
            if rng.random() < .07:
                ang, r = rng.uniform(0, 2 * math.pi), rng.uniform(9, 13)
                mp = (x + r * math.cos(ang), y + r * math.sin(ang))
                path += [(st - .04, *mp), (st, *mp), (st + .07, x, y)]
                misses.append((st, *mp))
                t = st + .07
            else:
                landed += 1
                hits_all.append((st, x, y))
                hits.append(st)
                s += 1
            acc_times.append(st)
            acc_labels.append(f"{round(100 * landed / fired)}%")
        hits_by[key] = hits
        kills.append((hits[-1], key))
        if lvl == 4:
            headshots.append(hits[-1])
        t += .08 + rng.uniform(0, .05)
        prev = (x, y)

    # sniper finale on the busiest day
    path.append((t, *prev))
    tx, ty = center(*mvp_key)
    sx, sy = 600, 150  # the scope pans the busiest day to the middle of the frame
    s_in = t + .35
    zoom_done = s_in + .4
    s_shot = zoom_done + 1.0
    s_out = s_shot + .55
    zoom_end = s_out + .3
    fired += 1
    landed += 1
    acc_times.append(s_shot)
    acc_labels.append(f"{round(100 * landed / fired)}%")
    hits_by[mvp_key] = [s_shot]
    kills.append((s_shot, mvp_key))
    path.append((zoom_end + .1, *home))
    win = (zoom_end + .3, zoom_end + 2.6)
    respawn = zoom_end + 2.4
    T = zoom_end + 2.9
    path.append((T, *home))

    # multi-kill and ACE callouts (rifle kills only): one callout per streak of 3+ kills in the same week,
    # shown on its last kill; ACE when that streak cleared the whole week
    streaks, prev_w, prev_kt = [], None, -9.0
    for kill_t, (w, d) in kills[:-1]:
        if w == prev_w and kill_t - prev_kt < .9:
            streaks[-1] = (w, streaks[-1][1] + 1, kill_t)
        else:
            streaks.append((w, 1, kill_t))
        prev_w, prev_kt = w, kill_t
    callouts = []
    for w, length, last_t in streaks:
        if length < 3:
            continue
        if length == week_active[w]:
            callouts.append((last_t, "ACE", w))
        else:
            callouts.append((last_t, {3: "TRIPLE KILL", 4: "QUADRA KILL"}.get(length, "MULTI KILL"), w))

    # ---- floating damage numbers: 100 hp per target, the last hit takes whatever is left;
    # consecutive hits on one target float up on alternating sides so they don't stack
    dmg_rng = random.Random(7)
    dmg = []
    for key, hits in hits_by.items():
        if key == mvp_key:
            continue
        x, y = center(*key)
        body = [dmg_rng.randint(24, 38) for _ in hits[:-1]]
        for i, (ht, amount) in enumerate(zip(hits, body + [100 - sum(body)])):
            hs = i == len(hits) - 1 and info[key][1] == 4
            x0, anchor = (x + 12, "start") if i % 2 == 0 else (x - 12, "end")
            y0 = y - 12
            y1 = max(62, y0 - 24)
            dmg.append(f'<text x="{x0:.0f}" y="{y0:.0f}" text-anchor="{anchor}" class="dmg{" hs" if hs else ""}" opacity="0">-{amount}'
                       f'{linear("opacity", [(0, 0), (ht - .001, 0), (ht, 1), (ht + .2, 1), (ht + .4, 0), (T, 0)], T)}'
                       f'{linear("y", [(0, round(y0)), (ht, round(y0)), (ht + .4, round(y1)), (T, round(y1))], T)}</text>')

    # ---- grid, hits, misses (everything the scope zooms into)
    grid = []
    for w, d, c, lvl, day in cells:
        grid.append(cell_rect(w, d, LEVELS[0], .9))
        if c:
            hits = hits_by[w, d]
            fill = [(0, LEVELS[lvl])] + [(ht, LEVELS[max(1, lvl - i - 1)]) for i, ht in enumerate(hits[:-1])] + [(respawn, LEVELS[lvl])]
            grid.append(cell_rect(w, d, LEVELS[lvl], 1, discrete("fill", fill, T)
                                  + discrete("opacity", [(0, 1), (hits[-1], 0), (respawn, 1)], T)))
    zoom = [(0, 1), (s_in, 1), (zoom_done, 2.4), (s_out, 2.4), (zoom_end, 1), (T, 1)]
    pan = [(0, tx, ty), (s_in, tx, ty), (zoom_done, sx, sy), (s_out, sx, sy), (zoom_end, tx, ty), (T, tx, ty)]
    scene = f"""<g>{moves(pan, T)}<g>{scale_anim(zoom, T)}<g transform="translate({-tx:.1f} {-ty:.1f})">
    <g>{''.join(grid)}</g>
    {burst_slots(hits_all, T)}
    {burst_slots(misses, T, slots=2, color=PINK)}
    {burst_slots([(s_shot, tx, ty)], T, slots=1, color=PINK)}
    <g>{''.join(dmg)}</g>
  </g></g></g>"""

    # ---- crosshair
    recoil = [(0, 1)]
    for st in rifle_shots:
        recoil += [(st, 1), (st + .03, 1.35), (st + .09, 1)]
    recoil.append((T, 1))
    crosshair = f"""<g>{moves(path, T)}{discrete("opacity", [(0, 1), (s_in - .02, 0), (zoom_end, 1)], T)}
    <g filter="url(#glow)">{scale_anim(recoil, T)}
      <circle r="13" fill="none" stroke="#eafffa" stroke-width="2"/>
      <path d="M-23,0 H-8 M8,0 H23 M0,-23 V-8 M0,8 V23" stroke="#eafffa" stroke-width="2"/>
      <circle r="2.2" fill="{PINK}"/>
    </g></g>"""

    # ---- radar: a minimap centred on the crosshair; targets drop off it as they die
    rs, rr = .35, 58
    dots = "".join(
        f'<circle cx="{center(w, d)[0]:.1f}" cy="{center(w, d)[1]:.1f}" r="{13 if (w, d) == mvp_key else 8}" fill="{PINK}">'
        f'{discrete("opacity", [(0, 1), (hits_by[w, d][-1], 0), (respawn, 1)], T)}</circle>'
        for w, d, *_ in active)
    radar = f"""<g transform="translate(1112 280)">
    <clipPath id="radarclip"><circle r="{rr}"/></clipPath>
    <circle r="{rr}" fill="#020605" fill-opacity=".92"/>
    <g clip-path="url(#radarclip)">
      <g transform="scale({rs})"><g>{moves([(t_, -x, -y) for t_, x, y in path], T)}
        <rect x="{X0 - 10}" y="{Y0 - 10}" width="{WEEKS * P + 15}" height="{DAYS * P + 15}" rx="10" fill="{TEAL}" fill-opacity=".07" stroke="{TEAL}" stroke-opacity=".3" stroke-width="3"/>
        {dots}
      </g></g>
      <path d="M0,0 L-34,-58 H34 Z" fill="#eafffa" fill-opacity=".06"/>
    </g>
    <circle r="{rr / 2}" fill="none" stroke="{TEAL}" stroke-opacity=".12"/>
    <circle r="{rr}" fill="none" stroke="{TEAL}" stroke-opacity=".45" stroke-width="1.5"/>
    <path d="M0,-6 L5,5 L0,2 L-5,5 Z" fill="#eafffa"/>
  </g>"""

    # ---- scope overlay
    sway = [(0, 22, -14), (s_in, 22, -14), (zoom_done, 10, -6), (zoom_done + .4, -6, 5), (zoom_done + .8, 4, -3),
            (s_shot - .08, 0, 0), (s_shot, 0, 0), (s_shot + .08, 0, -16), (s_out, 0, 0), (T, 0, 0)]
    defs = f"""<style>
    .dmg {{ font-size: 15px; font-weight: 700; fill: #eafffa; paint-order: stroke; stroke: #030607; stroke-width: 3px; }}
    .dmg.hs {{ font-size: 19px; fill: {PINK}; }}
  </style>
  <mask id="scopeMask" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}">
    <rect width="{W}" height="{H}" fill="#fff"/>
    <circle cx="{sx}" cy="{sy}" r="118" fill="#000">{linear("cx", [(t_, round(sx + dx, 1)) for t_, dx, _ in sway], T)}{linear("cy", [(t_, round(sy + dy, 1)) for t_, _, dy in sway], T)}</circle>
  </mask>"""
    sd = mvp[4]
    scope = f"""<g>{windows([(s_in, zoom_end)], T)}
    <rect width="{W}" height="{H}" fill="#010302" fill-opacity=".95" mask="url(#scopeMask)"/>
    <g>{moves([(t_, sx + dx, sy + dy) for t_, dx, dy in sway], T)}
      <circle r="118" fill="none" stroke="#000" stroke-width="7"/>
      <circle r="114" fill="none" stroke="{TEAL}" stroke-opacity=".35"/>
      <path d="M-118,0 H-10 M10,0 H118 M0,-118 V-10 M0,10 V118" stroke="#000" stroke-width="1.8"/>
      <path d="M-40,-3 V3 M-20,-3 V3 M20,-3 V3 M40,-3 V3 M-3,20 H3 M-3,40 H3" stroke="#000" stroke-width="1.5"/>
      <circle r="2" fill="{PINK}"/>
    </g>
    <rect width="{W}" height="{H}" fill="#fff">{linear("opacity", [(0, 0), (s_shot - .001, 0), (s_shot, .35), (s_shot + .12, 0), (T, 0)], T)}</rect>
    <text x="600" y="92" text-anchor="middle" font-size="40" font-weight="700" fill="{PINK}" filter="url(#glow)" letter-spacing="4">HEADSHOT{windows([(s_shot, s_shot + .9)], T)}</text>
    <text x="636" y="192" font-size="30" font-weight="700" fill="{PINK}" filter="url(#glow)">-100{windows([(s_shot, s_shot + .9)], T)}{linear("y", [(0, 192), (s_shot, 192), (s_shot + .9, 166), (T, 166)], T)}</text>
    <text x="600" y="{H - 40}" text-anchor="middle" class="txt">MVP · {MONTHS[sd.month - 1]} {sd.day} · {mvp[2]} contributions · {escape(short(repos.get(sd.isoformat(), 'private')))}{windows([(s_shot + .15, zoom_end)], T)}</text>
  </g>"""

    # ---- callouts, kill feed, round win
    popups = []
    for i, (ct, label, w) in enumerate(callouts):
        end = min(ct + .9, callouts[i + 1][0]) if i + 1 < len(callouts) else ct + .9
        ws = weeks[w][0][0]
        text = f"ACE · week of {MONTHS[ws.month - 1]} {ws.day}" if label == "ACE" else label
        color = PINK if label == "ACE" else TEAL
        width = 30 + len(text) * 17.4
        popups.append(f'<g>{windows([(ct, end)], T)}'
                      f'<rect x="{600 - width / 2:.1f}" y="92" width="{width:.1f}" height="40" rx="8" fill="#030607" fill-opacity=".88" stroke="{color}" stroke-opacity=".6"/>'
                      f'<text x="600" y="120" text-anchor="middle" font-size="24" font-weight="700" fill="{color}" letter-spacing="3" filter="url(#glow)">{text}</text></g>')

    feed_top, line = 250, 24
    feed = []
    for j, (_, key) in enumerate(kills, 1):
        c, lvl, day = info[key]
        tag = (f'<tspan fill="{PINK}" font-weight="700">  SNIPER HS</tspan>' if key == mvp_key
               else f'<tspan fill="{PINK}" font-weight="700">  HS</tspan>' if lvl == 4 else "")
        where = short(repos.get(day.isoformat(), "private"))
        feed.append(f'<text x="1040" y="{feed_top - j * line}" class="txt" text-anchor="end">'
                    f'<tspan fill="{TEAL}">{escape(login.lower())}</tspan><tspan fill="#5f8f84"> -╋- </tspan>{escape(where)} · {c}{tag}</text>')
    feed_pairs = [(0, 0)] + [(k_t, j * line) for j, (k_t, _) in enumerate(kills, 1)]
    feed_anim = ('<animateTransform attributeName="transform" type="translate" calcMode="discrete" '
                 f'values="{";".join(f"0 {v}" for _, v in feed_pairs)}" keyTimes="{";".join(kt(t_, T) for t_, _ in feed_pairs)}" '
                 f'dur="{T:.3f}s" repeatCount="indefinite"/>')

    n = len(active)
    acc_final = acc_labels[-1]
    all_hs = headshots + [s_shot]
    body = f"""
  {title_bar("aim_trainer.exe · contribution gridshot", f"{total} contributions · last 12 months")}
  {scene}
  {crosshair}
  <text x="600" y="182" text-anchor="middle" font-size="34" font-weight="700" fill="{PINK}" filter="url(#glow)" letter-spacing="4">HEADSHOT{windows([(x, x + .35) for x in headshots], T)}</text>
  {''.join(popups)}
  <clipPath id="feedclip"><rect x="520" y="{feed_top - 15}" width="530" height="{3 * line - 6}"/></clipPath>
  <g clip-path="url(#feedclip)"><g>{feed_anim}{''.join(feed)}</g></g>
  <text x="70" y="{feed_top}" class="dim">KILLS</text>
  {counter(132, feed_top, [k for k, _ in kills], T, [f"{i}/{n}" for i in range(n + 1)])}
  <text x="70" y="{feed_top + 32}" class="dim">HS</text>
  {counter(132, feed_top + 32, all_hs, T, [str(i) for i in range(len(all_hs) + 1)])}
  <text x="250" y="{feed_top}" class="dim">AMMO</text>
  {counter(308, feed_top, ammo_times, T, ammo_labels, width=60)}
  <text x="250" y="{feed_top + 32}" class="dim">ACC</text>
  {counter(308, feed_top + 32, acc_times, T, acc_labels, width=70)}
  <text x="400" y="{feed_top}" font-size="17" font-weight="700" fill="{PINK}">RELOADING{windows(reloads, T)}</text>
  {radar}
  <text x="70" y="{H - 24}" class="dim">1 target = 1 active day · busier days take more shots · busiest day gets the sniper</text>
  {scope}
  <g>{windows([win], T)}
    <rect x="56" y="62" width="1088" height="162" rx="10" fill="#030607" fill-opacity=".85" stroke="{TEAL}" stroke-opacity=".4"/>
    <text x="600" y="140" text-anchor="middle" font-size="46" font-weight="700" fill="{TEAL}" filter="url(#glow)" letter-spacing="6">ROUND WIN</text>
    <text x="600" y="180" text-anchor="middle" class="txt">{n}/{n} targets · {len(all_hs)} headshots · {acc_final} accuracy</text>
  </g>"""
    return frame(defs, body, f"Aim trainer clearing {n} active contribution days")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("login")
    ap.add_argument("out")
    ap.add_argument("--calendar", help="saved contributionCalendar GraphQL response instead of fetching")
    ap.add_argument("--repos", help="saved {date: repository} map instead of fetching")
    args = ap.parse_args()
    calendar = json.loads(Path(args.calendar).read_text(encoding="utf-8")) if args.calendar else graphql(CALENDAR, args.login)
    repos = json.loads(Path(args.repos).read_text(encoding="utf-8")) if args.repos else repo_days(graphql(WORK, args.login))
    total, weeks, cells = load_grid(calendar)
    if not any(c[2] for c in cells):
        raise SystemExit("no contributions in the last year, nothing to shoot")
    svg = aim_svg(total, weeks, cells, repos, args.login)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(svg, encoding="utf-8", newline="\n")
    print(f"wrote {out}: {total} contributions, {len(svg) // 1024} KB")
