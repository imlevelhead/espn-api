#!/usr/bin/env python3
"""
Live Fantasy Football Draft Assistant
=====================================

A real-time "war room" for your ESPN fantasy football draft, built on top of
the espn-api package. While your draft is running, it watches the picks come
in and tells you, on the clock, who the best pick is for *your* team.

What it does
------------
1. Before the draft: pulls every draftable player with ESPN's projected points
   and positional rank, then computes a value-over-replacement (VOR) board so
   you're comparing a top WR against a top RB fairly (not just raw points).
2. During the draft: every few seconds it re-reads the live draft, removes
   players who've been taken, tracks your roster, and prints:
     - whose turn it is / how many picks until you're up
     - your current roster and which starting slots you still need
     - the best available players overall (by VOR)
     - the best available at each position
     - a recommendation that blends value, your roster needs, and tier scarcity
       ("last elite RB on the board — grab him now")

It never makes a pick for you (that would violate ESPN's terms and the API is
read-only). It's a co-pilot: it tells you what to click, you click it.

Setup
-----
1. Install the package (from the repo root):
       pip install -r requirementsV2.txt
   or
       pip install espn_api

2. Fill in the CONFIG section below. For a PRIVATE league you need two cookies,
   ESPN_S2 and SWID. To get them (Chrome/Edge):
     - Log in to fantasy.espn.com in your browser.
     - Open DevTools (F12) > Application tab > Cookies > https://fantasy.espn.com
     - Copy the *Value* of `espn_s2` and `SWID` (include the { } braces on SWID).

3. Run it once BEFORE the draft to confirm it connects and builds the board:
       python examples/draft_assistant.py
   Leave it running during the draft; it refreshes on its own.

Tip: run it a day early against last year's league_id/year just to see the
output format with a completed draft.
"""

import time
import os
from collections import defaultdict

from espn_api.football import League

# ---------------------------------------------------------------------------
# CONFIG — edit these
# ---------------------------------------------------------------------------

LEAGUE_ID = 123456          # your ESPN league id (in the league URL: leagueId=...)
YEAR = 2026                 # draft season

# Private league cookies (leave as None only if the league is public).
# You can also set these as environment variables ESPN_S2 / SWID instead of
# pasting them here.
ESPN_S2 = os.environ.get("ESPN_S2") or None
SWID = os.environ.get("SWID") or None

# Who are you? Set ONE of these so the assistant knows which team is yours.
#   - MY_TEAM_NAME: exact team name as it appears in ESPN (case-insensitive), or
#   - MY_TEAM_ID:   the numeric team id (1..N). Leave the other as None.
MY_TEAM_NAME = None
MY_TEAM_ID = None

# Optional: your draft slot (1 = first overall). If left as None the assistant
# figures it out automatically once your first-round pick happens.
DRAFT_SLOT = None

# How often to re-check the live draft, in seconds.
REFRESH_SECONDS = 8

# Scoring: does your league use PPR? Only affects the tiny nudge given to pass
# catchers; ESPN's projections already bake in your league's scoring.
# (Informational only — projections come straight from ESPN.)

# How many players to pull into the board. 600 comfortably covers any draft.
BOARD_SIZE = 600

# ---------------------------------------------------------------------------
# Starting-lineup assumptions (used for value-over-replacement math).
# These are auto-read from your league settings; the values below are only a
# fallback if the settings can't be read.
# ---------------------------------------------------------------------------
DEFAULT_STARTERS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "D/ST": 1, "K": 1}
FLEX_POSITIONS = ("RB", "WR", "TE")  # who competes for FLEX spots

SCORING_POSITIONS = ["QB", "RB", "WR", "TE", "D/ST", "K"]


# ---------------------------------------------------------------------------
# Board building
# ---------------------------------------------------------------------------
def normalize_position(pos):
    """Collapse ESPN eligible slots to a single primary fantasy position."""
    if pos in SCORING_POSITIONS:
        return pos
    return pos  # already normalized upstream


def build_board(league):
    """Pull all draftable players once and return a list of dicts with a VOR."""
    print("Building draft board from ESPN projections...")
    players = league.free_agents(size=BOARD_SIZE)

    board = []
    for p in players:
        pos = p.position if p.position in SCORING_POSITIONS else (p.position or "?")
        proj = float(getattr(p, "projected_total_points", 0) or 0)
        board.append({
            "id": p.playerId,
            "name": p.name,
            "pos": pos,
            "team": p.proTeam,
            "proj": proj,
            "posRank": getattr(p, "posRank", 0) or 0,
            "pct_owned": getattr(p, "percent_owned", 0) or 0,
            "injury": getattr(p, "injuryStatus", None),
        })

    # Sort each position by projection to find the "replacement level" player.
    by_pos = defaultdict(list)
    for pl in board:
        by_pos[pl["pos"]].append(pl)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x["proj"], reverse=True)

    starters = get_starters(league)
    n_teams = len(league.teams) or 10

    # Replacement rank per position = starters at that position across the whole
    # league, plus a share of the FLEX spots for RB/WR/TE.
    flex_slots = starters.get("FLEX", 0)
    flex_share = (flex_slots * n_teams) / len(FLEX_POSITIONS) if flex_slots else 0

    replacement = {}
    for pos in by_pos:
        base = starters.get(pos, 0) * n_teams
        if pos in FLEX_POSITIONS:
            base += flex_share
        # replacement player is the one just past the last startable slot
        idx = max(0, int(round(base)) - 1)
        pool = by_pos[pos]
        if pool:
            idx = min(idx, len(pool) - 1)
            replacement[pos] = pool[idx]["proj"]
        else:
            replacement[pos] = 0

    for pl in board:
        pl["vor"] = round(pl["proj"] - replacement.get(pl["pos"], 0), 1)

    board.sort(key=lambda x: x["vor"], reverse=True)
    print(f"Board built: {len(board)} players across {len(by_pos)} positions.\n")
    return board


def get_starters(league):
    """Read starting-slot counts from league settings, with a safe fallback."""
    try:
        counts = league.settings.position_slot_counts
        starters = {}
        for pos in SCORING_POSITIONS:
            starters[pos] = int(counts.get(pos, DEFAULT_STARTERS.get(pos, 0)))
        # FLEX spots (RB/WR/TE and RB/WR)
        flex = 0
        for key in ("RB/WR/TE", "RB/WR", "FLEX"):
            flex += int(counts.get(key, 0))
        starters["FLEX"] = flex
        # If ESPN returned all zeros for some reason, fall back.
        if sum(starters.values()) == 0:
            raise ValueError
        return starters
    except Exception:
        s = dict(DEFAULT_STARTERS)
        s["FLEX"] = 1
        return s


# ---------------------------------------------------------------------------
# Live draft state
# ---------------------------------------------------------------------------
def resolve_my_team(league):
    """Return the Team object that is 'me' based on config."""
    if MY_TEAM_ID is not None:
        for t in league.teams:
            if t.team_id == MY_TEAM_ID:
                return t
    if MY_TEAM_NAME:
        for t in league.teams:
            if t.team_name.strip().lower() == MY_TEAM_NAME.strip().lower():
                return t
    return None


def refresh_picks(league):
    """Re-read the live draft. league.draft is rebuilt (it otherwise appends)."""
    league.draft = []          # _fetch_draft appends, so clear first
    league.refresh_draft()
    return league.draft


def my_draft_slot(league, my_team, picks):
    """Figure out your snake draft slot (1..N)."""
    if DRAFT_SLOT:
        return DRAFT_SLOT
    if my_team is None:
        return None
    for pick in picks:
        if pick.round_num == 1 and pick.team is not None and pick.team.team_id == my_team.team_id:
            return pick.round_pick
    return None


def picks_until_my_turn(slot, n_teams, picks_made):
    """In a snake draft, how many picks until it's your turn (0 = you're up)."""
    if not slot:
        return None
    nxt = None
    r = 1
    while True:
        if r % 2 == 1:
            overall = (r - 1) * n_teams + slot
        else:
            overall = (r - 1) * n_teams + (n_teams - slot + 1)
        if overall > picks_made:
            nxt = overall
            break
        r += 1
        if r > 40:  # safety
            return None
    return nxt - picks_made - 1


# ---------------------------------------------------------------------------
# Recommendation logic
# ---------------------------------------------------------------------------
def roster_needs(my_players, starters):
    """Count how many starting slots you still have open per position."""
    have = defaultdict(int)
    for pl in my_players:
        have[pl["pos"]] += 1
    needs = {}
    for pos in SCORING_POSITIONS:
        needs[pos] = max(0, starters.get(pos, 0) - have[pos])
    # FLEX open if you don't yet have surplus RB/WR/TE beyond their base starters
    flex_surplus = sum(max(0, have[p] - starters.get(p, 0)) for p in FLEX_POSITIONS)
    needs["FLEX"] = max(0, starters.get("FLEX", 0) - flex_surplus)
    return needs


def recommend(board, drafted_ids, my_players, starters):
    """Return (ranked recommendations, best-by-position, tier flags)."""
    available = [p for p in board if p["id"] not in drafted_ids]
    needs = roster_needs(my_players, starters)

    # Best available at each position (by VOR).
    best_by_pos = {}
    for pos in SCORING_POSITIONS:
        pool = [p for p in available if p["pos"] == pos]
        if pool:
            best_by_pos[pos] = pool[:5]

    # Score each available player: VOR, boosted by roster need, and by tier
    # scarcity (few comparable players left at the position = grab now).
    scored = []
    for p in available[:80]:  # only need to consider the top of the board
        pos = p["pos"]
        need = needs.get(pos, 0)
        # need multiplier: strong bump for an unfilled starter, small for FLEX-only
        if need > 0:
            need_mult = 1.15
        elif pos in FLEX_POSITIONS and needs.get("FLEX", 0) > 0:
            need_mult = 1.05
        else:
            need_mult = 0.85  # already set at this position; deprioritize a bit

        # tier scarcity: how many available at this position within 15 pts of VOR
        same_tier = [q for q in available
                     if q["pos"] == pos and abs(q["vor"] - p["vor"]) <= 15]
        scarce = len(same_tier) <= 3
        scarce_mult = 1.10 if scarce else 1.0

        score = p["vor"] * need_mult * scarce_mult
        scored.append((score, scarce, need > 0, p))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:8], best_by_pos, needs


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def clear():
    os.system("cls" if os.name == "nt" else "clear")


def fmt_player(p):
    inj = f" ({p['injury']})" if p.get("injury") and p["injury"] not in ("ACTIVE", None) else ""
    return f"{p['name']:22.22} {p['pos']:5} {p['team']:4} proj {p['proj']:6.1f}  VOR {p['vor']:6.1f}{inj}"


def render(league, my_team, board, picks):
    drafted_ids = {pick.playerId for pick in picks}
    starters = get_starters(league)
    n_teams = len(league.teams)

    # my roster (from picks assigned to my team)
    id_to_board = {p["id"]: p for p in board}
    my_players = []
    if my_team is not None:
        for pick in picks:
            if pick.team is not None and pick.team.team_id == my_team.team_id:
                bp = id_to_board.get(pick.playerId)
                if bp:
                    my_players.append(bp)

    slot = my_draft_slot(league, my_team, picks)
    until = picks_until_my_turn(slot, n_teams, len(picks))

    recs, best_by_pos, needs = recommend(board, drafted_ids, my_players, starters)

    clear()
    print("=" * 68)
    print(f" DRAFT WAR ROOM — {league.league_id} / {league.year}"
          f"   picks made: {len(picks)}")
    if my_team is not None:
        turn = ("YOU'RE ON THE CLOCK ⏰" if until == 0
                else f"{until} pick(s) until your turn" if until is not None
                else "waiting for your first pick...")
        print(f" You: {my_team.team_name}  (slot {slot})   {turn}")
    print("=" * 68)

    # Roster + needs
    if my_team is not None:
        need_str = " ".join(f"{k}:{v}" for k, v in needs.items() if v > 0) or "none — all starters filled"
        print(f"\n YOUR ROSTER ({len(my_players)}):")
        if my_players:
            for p in my_players:
                print("   " + fmt_player(p))
        else:
            print("   (empty)")
        print(f"\n STILL NEED (starters): {need_str}")

    # Recommendation
    print("\n RECOMMENDED PICKS (value + your needs + scarcity):")
    if recs:
        for i, (score, scarce, needed, p) in enumerate(recs, 1):
            tags = []
            if needed:
                tags.append("fills need")
            if scarce:
                tags.append("tier thinning")
            tag = f"  <- {', '.join(tags)}" if tags else ""
            print(f"  {i}. " + fmt_player(p) + tag)
    else:
        print("   (no players available — is the draft over?)")

    # Best by position
    print("\n BEST AVAILABLE BY POSITION:")
    for pos in SCORING_POSITIONS:
        pool = best_by_pos.get(pos)
        if pool:
            top = pool[0]
            print(f"   {pos:5}: " + fmt_player(top))

    print("\n (refreshing every "
          f"{REFRESH_SECONDS}s — Ctrl+C to quit)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    if LEAGUE_ID == 123456:
        print("!! Edit the CONFIG section first: set LEAGUE_ID, YEAR, and (for a "
              "private league) ESPN_S2 + SWID, and identify your team.")
        return

    print("Connecting to ESPN...")
    league = League(league_id=LEAGUE_ID, year=YEAR, espn_s2=ESPN_S2, swid=SWID)
    print(f"Connected: {len(league.teams)} teams.")

    my_team = resolve_my_team(league)
    if my_team is None:
        print("\nNote: couldn't match your team from MY_TEAM_NAME / MY_TEAM_ID.")
        print("Teams in this league:")
        for t in league.teams:
            print(f"   id={t.team_id}  {t.team_name}")
        print("Set MY_TEAM_ID (or MY_TEAM_NAME) in CONFIG so recommendations "
              "are tailored to you. Continuing with a generic board.\n")
        time.sleep(3)

    board = build_board(league)

    try:
        while True:
            picks = refresh_picks(league)
            render(league, my_team, board, picks)
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        print("\nGood luck — go win your league. 🏆")


if __name__ == "__main__":
    main()
