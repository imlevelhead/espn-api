#!/usr/bin/env python3
"""
espn-draft tool — the data layer behind the espn-draft skill.

A small CLI that talks to ESPN's fantasy API (via the espn_api package) so
Claude can watch a live draft and advise on picks. It is deliberately quiet
and structured: each command prints a compact, LLM-friendly block that a
subagent can relay to the main session without dragging raw JSON along.

State lives in a local, git-ignored directory (default: ./.espn-draft/):
  config.json  - league id, year, cookies, your team
  board.json   - the projection board, built once before the draft
  state.json   - last-seen pick count (so `poll` can print only the delta)

Commands
--------
  init      Create a template config.json (edit it, or use env vars).
  teams     List teams in the league (id + name) so you can identify yours.
  board     Build the value-over-replacement board and cache it. Run once
            BEFORE the draft. --pos QB/RB/... and --limit N filter the printout.
  poll      Fetch the live draft and print ONLY what changed since last poll:
            new picks, whose turn it is, your roster/needs, top available.
            This is the command the watch loop runs each tick.
  status    Full current snapshot (roster, needs, best available by position)
            without the delta logic — for on-demand "where do things stand".
  reset     Forget the poll cursor (state.json) so the next poll re-reports.

Config resolution: config.json values, then environment variables
(ESPN_S2, SWID, ESPN_LEAGUE_ID, ESPN_YEAR, ESPN_TEAM_ID, ESPN_TEAM_NAME).
"""

import argparse
import json
import os
import sys
from collections import defaultdict

# Make the tool work whether espn_api is pip-installed OR we're running from
# inside the cloned espn-api repo: walk up from cwd and from this script's
# location looking for a directory that contains the espn_api package.
def _bootstrap_espn_api_path():
    starts = [os.getcwd(), os.path.dirname(os.path.abspath(__file__))]
    for start in starts:
        cur = start
        for _ in range(8):
            if os.path.isdir(os.path.join(cur, "espn_api")):
                if cur not in sys.path:
                    sys.path.insert(0, cur)
                return
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent

_bootstrap_espn_api_path()

try:
    from espn_api.football import League
except ImportError:
    sys.exit("espn_api is not installed. Run:  pip install espn_api  "
             "(or run this from inside the cloned espn-api repo).")

SCORING_POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]
FLEX_POSITIONS = ("RB", "WR", "TE")
DEFAULT_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1, "FLEX": 1}

STATE_DIR = os.environ.get("ESPN_DRAFT_DIR", ".espn-draft")


# --------------------------------------------------------------------------
# Config / state files
# --------------------------------------------------------------------------
def paths():
    return {
        "config": os.path.join(STATE_DIR, "config.json"),
        "board": os.path.join(STATE_DIR, "board.json"),
        "state": os.path.join(STATE_DIR, "state.json"),
    }


def load_json(path, default=None):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_config():
    cfg = load_json(paths()["config"], {}) or {}
    # env var overrides / fallbacks
    env = os.environ
    cfg.setdefault("league_id", env.get("ESPN_LEAGUE_ID"))
    cfg.setdefault("year", env.get("ESPN_YEAR"))
    cfg.setdefault("espn_s2", env.get("ESPN_S2"))
    cfg.setdefault("swid", env.get("SWID"))
    cfg.setdefault("team_id", env.get("ESPN_TEAM_ID"))
    cfg.setdefault("team_name", env.get("ESPN_TEAM_NAME"))
    cfg.setdefault("draft_slot", None)
    cfg.setdefault("board_size", 600)
    # coerce numeric
    for k in ("league_id", "year", "team_id", "draft_slot", "board_size"):
        if cfg.get(k) not in (None, ""):
            try:
                cfg[k] = int(cfg[k])
            except (ValueError, TypeError):
                pass
    return cfg


def connect(cfg):
    if not cfg.get("league_id") or not cfg.get("year"):
        sys.exit("Missing league_id/year. Run `draft.py init` and edit "
                 f"{paths()['config']} (or set ESPN_LEAGUE_ID / ESPN_YEAR).")
    return League(
        league_id=cfg["league_id"],
        year=cfg["year"],
        espn_s2=cfg.get("espn_s2") or None,
        swid=cfg.get("swid") or None,
    )


# --------------------------------------------------------------------------
# Board / value math
# --------------------------------------------------------------------------
def get_starters(league):
    try:
        counts = league.settings.position_slot_counts
        starters = {p: int(counts.get(p, DEFAULT_STARTERS.get(p, 0)))
                    for p in SCORING_POSITIONS}
        flex = sum(int(counts.get(k, 0)) for k in ("RB/WR/TE", "RB/WR", "FLEX"))
        starters["FLEX"] = flex
        if sum(starters.values()) == 0:
            raise ValueError
        return starters
    except Exception:
        return dict(DEFAULT_STARTERS)


def build_board(league, cfg):
    players = league.free_agents(size=cfg.get("board_size", 600))
    board = []
    for p in players:
        pos = p.position if p.position in SCORING_POSITIONS else (p.position or "?")
        board.append({
            "id": p.playerId,
            "name": p.name,
            "pos": pos,
            "team": p.proTeam,
            "proj": round(float(getattr(p, "projected_total_points", 0) or 0), 1),
            "posRank": getattr(p, "posRank", 0) or 0,
            "pct_owned": getattr(p, "percent_owned", 0) or 0,
            "injury": getattr(p, "injuryStatus", None),
        })

    by_pos = defaultdict(list)
    for pl in board:
        by_pos[pl["pos"]].append(pl)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x["proj"], reverse=True)

    starters = get_starters(league)
    n_teams = len(league.teams) or 10
    flex_slots = starters.get("FLEX", 0)
    flex_share = (flex_slots * n_teams) / len(FLEX_POSITIONS) if flex_slots else 0

    replacement = {}
    for pos, pool in by_pos.items():
        base = starters.get(pos, 0) * n_teams
        if pos in FLEX_POSITIONS:
            base += flex_share
        idx = min(max(0, int(round(base)) - 1), len(pool) - 1) if pool else 0
        replacement[pos] = pool[idx]["proj"] if pool else 0

    for pl in board:
        pl["vor"] = round(pl["proj"] - replacement.get(pl["pos"], 0), 1)
    board.sort(key=lambda x: x["vor"], reverse=True)
    return board


# --------------------------------------------------------------------------
# Roster / needs / snake timing
# --------------------------------------------------------------------------
def resolve_my_team(league, cfg):
    if cfg.get("team_id"):
        for t in league.teams:
            if t.team_id == cfg["team_id"]:
                return t
    if cfg.get("team_name"):
        for t in league.teams:
            if t.team_name.strip().lower() == str(cfg["team_name"]).strip().lower():
                return t
    return None


def refresh_picks(league):
    league.draft = []            # _fetch_draft appends; clear to avoid dupes
    league.refresh_draft()
    return league.draft


def my_draft_slot(cfg, my_team, picks):
    if cfg.get("draft_slot"):
        return cfg["draft_slot"]
    if my_team is None:
        return None
    for pick in picks:
        if pick.round_num == 1 and pick.team is not None and pick.team.team_id == my_team.team_id:
            return pick.round_pick
    return None


def picks_until_my_turn(slot, n_teams, picks_made):
    if not slot:
        return None
    r = 1
    while r <= 40:
        overall = ((r - 1) * n_teams + slot if r % 2 == 1
                   else (r - 1) * n_teams + (n_teams - slot + 1))
        if overall > picks_made:
            return overall - picks_made - 1
        r += 1
    return None


def roster_needs(my_players, starters):
    have = defaultdict(int)
    for pl in my_players:
        have[pl["pos"]] += 1
    needs = {p: max(0, starters.get(p, 0) - have[p]) for p in SCORING_POSITIONS}
    flex_surplus = sum(max(0, have[p] - starters.get(p, 0)) for p in FLEX_POSITIONS)
    needs["FLEX"] = max(0, starters.get("FLEX", 0) - flex_surplus)
    return needs


def recommend(board, drafted_ids, my_players, starters, top=8):
    available = [p for p in board if p["id"] not in drafted_ids]
    needs = roster_needs(my_players, starters)
    best_by_pos = {}
    for pos in SCORING_POSITIONS:
        pool = [p for p in available if p["pos"] == pos]
        if pool:
            best_by_pos[pos] = pool[:5]
    scored = []
    for p in available[:80]:
        pos = p["pos"]
        need = needs.get(pos, 0)
        if need > 0:
            need_mult = 1.15
        elif pos in FLEX_POSITIONS and needs.get("FLEX", 0) > 0:
            need_mult = 1.05
        else:
            need_mult = 0.85
        same_tier = [q for q in available
                     if q["pos"] == pos and abs(q["vor"] - p["vor"]) <= 15]
        scarce = len(same_tier) <= 3
        score = p["vor"] * need_mult * (1.10 if scarce else 1.0)
        scored.append((round(score, 1), scarce, need > 0, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top], best_by_pos, needs


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
def pl_line(p):
    inj = f" [{p['injury']}]" if p.get("injury") and p["injury"] not in ("ACTIVE", None) else ""
    return f"{p['name']} ({p['pos']}, {p['team']}) proj {p['proj']} VOR {p['vor']}{inj}"


def gather(cfg, league, board):
    """Common computation for poll/status."""
    picks = refresh_picks(league)
    drafted_ids = {pk.playerId for pk in picks}
    starters = get_starters(league)
    n_teams = len(league.teams)
    id_to_board = {p["id"]: p for p in board}
    my_team = resolve_my_team(league, cfg)
    my_players = []
    if my_team is not None:
        for pk in picks:
            if pk.team is not None and pk.team.team_id == my_team.team_id:
                bp = id_to_board.get(pk.playerId)
                my_players.append(bp or {"id": pk.playerId, "name": pk.playerName or str(pk.playerId),
                                         "pos": "?", "team": "?", "proj": 0, "vor": 0})
    slot = my_draft_slot(cfg, my_team, picks)
    until = picks_until_my_turn(slot, n_teams, len(picks))
    recs, best_by_pos, needs = recommend(board, drafted_ids, my_players, starters)
    return dict(picks=picks, my_team=my_team, my_players=my_players, slot=slot,
                until=until, recs=recs, best_by_pos=best_by_pos, needs=needs,
                n_teams=n_teams)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def cmd_init(args):
    p = paths()["config"]
    if os.path.exists(p) and not args.force:
        print(f"Config already exists at {p} (use --force to overwrite).")
        return
    template = {
        "league_id": 0,
        "year": 2026,
        "espn_s2": "",
        "swid": "",
        "team_id": None,
        "team_name": None,
        "draft_slot": None,
        "board_size": 600,
    }
    save_json(p, template)
    print(f"Wrote template config to {p}")
    print("Edit it: set league_id, year, and (private league) espn_s2 + swid.")
    print("Then run `draft.py teams` to find your team id.")


def cmd_teams(args):
    cfg = load_config()
    league = connect(cfg)
    print(f"TEAMS in league {cfg['league_id']} ({cfg['year']}):")
    for t in league.teams:
        print(f"  id={t.team_id:>2}  {t.team_name}")
    print("\nSet your team_id in config.json (or ESPN_TEAM_ID).")


def cmd_board(args):
    cfg = load_config()
    league = connect(cfg)
    board = build_board(league, cfg)
    save_json(paths()["board"], board)
    print(f"Board built and cached: {len(board)} players "
          f"({paths()['board']}).")
    pool = board
    if args.pos:
        pool = [p for p in board if p["pos"] == args.pos.upper()]
    print(f"\nTOP {min(args.limit, len(pool))} BY VOR"
          + (f" ({args.pos.upper()})" if args.pos else "") + ":")
    for i, p in enumerate(pool[:args.limit], 1):
        print(f"  {i:>3}. " + pl_line(p))


def _load_or_build_board(cfg, league):
    board = load_json(paths()["board"])
    if not board:
        board = build_board(league, cfg)
        save_json(paths()["board"], board)
    return board


def cmd_poll(args):
    cfg = load_config()
    league = connect(cfg)
    board = _load_or_build_board(cfg, league)
    g = gather(cfg, league, board)
    picks = g["picks"]
    state = load_json(paths()["state"], {"last_pick_count": 0}) or {"last_pick_count": 0}
    last = state.get("last_pick_count", 0)
    new_picks = picks[last:] if len(picks) >= last else picks

    print(f"=== DRAFT POLL === picks_made={len(picks)} (new since last: {len(new_picks)})")
    if g["my_team"] is not None:
        turn = ("ON THE CLOCK NOW" if g["until"] == 0
                else f"{g['until']} picks away" if g["until"] is not None
                else "your slot unknown (waiting for your round-1 pick)")
        print(f"you={g['my_team'].team_name} slot={g['slot']} -> {turn}")

    if new_picks:
        print("\nNEW PICKS:")
        for pk in new_picks:
            tname = pk.team.team_name if pk.team else "?"
            print(f"  R{pk.round_num}.{pk.round_pick}  {pk.playerName or pk.playerId}  -> {tname}")
    else:
        print("\nNEW PICKS: (none since last poll)")

    if g["my_team"] is not None:
        need_str = " ".join(f"{k}:{v}" for k, v in g["needs"].items() if v > 0) or "starters full"
        print(f"\nYOUR ROSTER ({len(g['my_players'])}): "
              + (", ".join(f"{p['name']}({p['pos']})" for p in g["my_players"]) or "empty"))
        print(f"STILL NEED: {need_str}")

    print("\nTOP AVAILABLE (value x need x scarcity):")
    for i, (score, scarce, needed, p) in enumerate(g["recs"], 1):
        tags = []
        if needed:
            tags.append("need")
        if scarce:
            tags.append("scarce")
        print(f"  {i}. " + pl_line(p) + (f"  <{','.join(tags)}>" if tags else ""))

    print("\nBEST BY POSITION:")
    for pos in SCORING_POSITIONS:
        pool = g["best_by_pos"].get(pos)
        if pool:
            print(f"  {pos:>4}: " + pl_line(pool[0]))

    if not args.no_advance:
        save_json(paths()["state"], {"last_pick_count": len(picks)})


def cmd_status(args):
    cfg = load_config()
    league = connect(cfg)
    board = _load_or_build_board(cfg, league)
    g = gather(cfg, league, board)
    print(f"=== DRAFT STATUS === picks_made={len(g['picks'])}")
    if g["my_team"] is not None:
        turn = ("ON THE CLOCK NOW" if g["until"] == 0
                else f"{g['until']} picks away" if g["until"] is not None else "slot unknown")
        need_str = " ".join(f"{k}:{v}" for k, v in g["needs"].items() if v > 0) or "starters full"
        print(f"you={g['my_team'].team_name} slot={g['slot']} -> {turn}")
        print(f"ROSTER ({len(g['my_players'])}): "
              + (", ".join(f"{p['name']}({p['pos']})" for p in g["my_players"]) or "empty"))
        print(f"STILL NEED: {need_str}")
    print("\nTOP AVAILABLE:")
    for i, (score, scarce, needed, p) in enumerate(g["recs"], 1):
        print(f"  {i}. " + pl_line(p))
    print("\nBEST BY POSITION:")
    for pos in SCORING_POSITIONS:
        pool = g["best_by_pos"].get(pos)
        if pool:
            print(f"  {pos:>4}: " + pl_line(pool[0]))


def cmd_reset(args):
    save_json(paths()["state"], {"last_pick_count": 0})
    print("Poll cursor reset. Next poll will re-report all picks.")


def main():
    ap = argparse.ArgumentParser(description="espn-draft tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").add_argument("--force", action="store_true")
    sub.add_parser("teams")
    b = sub.add_parser("board")
    b.add_argument("--pos", default=None)
    b.add_argument("--limit", type=int, default=30)
    pp = sub.add_parser("poll")
    pp.add_argument("--no-advance", action="store_true",
                    help="don't move the cursor (peek without consuming the delta)")
    sub.add_parser("status")
    sub.add_parser("reset")

    args = ap.parse_args()
    {
        "init": cmd_init, "teams": cmd_teams, "board": cmd_board,
        "poll": cmd_poll, "status": cmd_status, "reset": cmd_reset,
    }[args.cmd](args)


if __name__ == "__main__":
    main()
