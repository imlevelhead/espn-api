# Live Draft Assistant

A real-time draft "war room" built on `espn-api`. During your ESPN fantasy
football draft it watches the picks come in and tells you, on the clock, the
best pick for **your** team — blending player value, your roster needs, and
positional scarcity.

It's a co-pilot, not an autopilot: the ESPN API is read-only, so it tells you
who to click; you make the pick.

## Easiest: run it in your browser (no install) — Google Colab

You don't need Python or a terminal. Open the notebook in Google Colab (free,
runs on Google's servers) and just fill in your league details:

**[▶ Open in Colab](https://colab.research.google.com/github/imlevelhead/espn-api/blob/claude/repo-draft-help-h3i9nw/examples/draft_assistant_colab.ipynb)**

Then: run Cell 1 (setup), fill in your league id / year / cookies / team in
Cell 2, run Cell 3, and leave the tab open during your draft — it refreshes
itself. Stop it with the ⏹ button.

> Privacy: your ESPN cookies stay in your own Colab session. Don't share the
> notebook with cookies filled in, and use *Runtime → Disconnect and delete
> runtime* when you're done.

## Quick start (running locally)

1. **Install** (from the repo root):
   ```bash
   pip install -r requirementsV2.txt
   ```

2. **Get your private-league cookies** (`ESPN_S2` and `SWID`):
   - Log in at <https://fantasy.espn.com> in Chrome/Edge.
   - Open DevTools (`F12`) → **Application** → **Cookies** → `https://fantasy.espn.com`.
   - Copy the *Value* of `espn_s2` and of `SWID` (keep the `{ }` braces on SWID).

   Then either paste them into the `CONFIG` block in `draft_assistant.py`, or
   export them as environment variables:
   ```bash
   export ESPN_S2='AEB...long...string'
   export SWID='{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}'
   ```

3. **Edit `CONFIG`** at the top of `examples/draft_assistant.py`:
   - `LEAGUE_ID`, `YEAR`
   - Identify yourself with `MY_TEAM_NAME` **or** `MY_TEAM_ID`.
     (Run it once; if it can't match you it prints every team's id/name.)

4. **Run it** — start it a few minutes before the draft and leave it up:
   ```bash
   python examples/draft_assistant.py
   ```
   It refreshes every few seconds on its own. `Ctrl+C` to quit.

## What you'll see

```
====================================================================
 DRAFT WAR ROOM — 123456 / 2026   picks made: 17
 You: Griddy Boys  (slot 3)   1 pick(s) until your turn
====================================================================

 YOUR ROSTER (2):
   Bijan Robinson        RB    ATL  proj  305.1  VOR  118.4
   CeeDee Lamb           WR    DAL  proj  298.7  VOR  102.9

 STILL NEED (starters): WR:1 TE:1 QB:1 FLEX:1

 RECOMMENDED PICKS (value + your needs + scarcity):
  1. Puka Nacua            WR    LAR  proj  268.0  VOR   72.1  <- fills need, tier thinning
  2. Jahmyr Gibbs          RB    DET  proj  272.4  VOR   85.0
  ...

 BEST AVAILABLE BY POSITION:
   QB   : Josh Allen ...
   RB   : Jahmyr Gibbs ...
   ...
```

## How the recommendation is calculated

- **Value over replacement (VOR)** — instead of raw projected points, each
  player is scored against the "replacement-level" player at their position
  (the guy you could stream/draft late). This is what lets you compare a top
  RB fairly against a top WR or QB. Starter counts and FLEX spots are read from
  your league settings.
- **Roster need** — a position you still need a *starter* at gets a boost; a
  position you've already filled gets nudged down.
- **Tier scarcity** — if only a few comparable players remain at a position,
  it's flagged `tier thinning` so you grab the value before it falls off a cliff.

Projections come straight from ESPN and already reflect your league's scoring.

## Try it before the draft

Point it at a **completed** past season (`YEAR = 2024`, last year's
`LEAGUE_ID`) to see the full output against a finished draft — a safe way to
confirm your cookies work and get used to the display.

## Limitations & honesty

- **Read-only.** It cannot and will not make picks for you.
- **Only as good as ESPN's projections.** Treat it as one strong input, not
  gospel — your own reads on injuries, situations, and sleepers still matter.
- **Live data timing.** It relies on ESPN publishing each pick to their draft
  API; there can be a few seconds of lag. The `picks made` counter tells you if
  it's keeping up.
- **Snake drafts.** The "picks until your turn" math assumes a standard snake
  order. Auction drafts show value/needs but not turn timing.
