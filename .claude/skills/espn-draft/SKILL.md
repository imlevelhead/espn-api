---
name: espn-draft
description: >
  Live fantasy football draft co-pilot for ESPN leagues. Use when the user
  wants help drafting — watching their ESPN draft as picks happen and advising
  who to pick on the clock. Connects to ESPN via the espn_api package, polls the
  live draft, and recommends picks based on value, roster needs, and scarcity.
  Triggers: "help me draft", "draft assistant", "watch my draft", "who should I
  pick", "start my draft", fantasy football draft.
---

# ESPN Live Draft Co-Pilot

You are the user's draft brain. A small CLI (`scripts/draft.py`) is your data
layer to ESPN; **you** provide the judgment. This skill runs locally in the
user's terminal (ESPN's API is not reachable from cloud sessions).

## The tool

Run everything through:

```
python .claude/skills/espn-draft/scripts/draft.py <command>
```

Commands: `init`, `teams`, `board [--pos RB --limit 30]`, `poll [--no-advance]`,
`status`, `reset`. Config + cached board + poll cursor live in `.espn-draft/`
(git-ignored — it holds the user's ESPN cookies). Full details: `scripts/README`
is inline in `draft.py`'s docstring.

## Two phases

### Phase 1 — Setup (before the draft)

Do this once, walking the user through it:

1. **Install** (if needed): `pip install espn_api`
2. **Config**: run `... draft.py init`, then have the user fill `.espn-draft/config.json`:
   - `league_id`, `year`
   - `espn_s2` + `swid` (private league cookies — DevTools → Application →
     Cookies → fantasy.espn.com; keep the `{ }` on SWID)
3. **Identify their team**: run `... draft.py teams`, ask which is theirs, and
   write `team_id` into the config.
4. **Build the board**: run `... draft.py board`. This caches ESPN projections
   with a value-over-replacement (VOR) number. Do this shortly before the draft
   so the player pool is complete.
5. **Draft plan**: ask the user 2–3 quick questions (scoring/PPR, any strategy
   lean like Zero-RB or best-ball, players they love/avoid). Write a short
   **`.espn-draft/strategy.md`** capturing: their draft slot (if known),
   positional plan by round tier, target players, and hard avoids. This file is
   your durable memory — keep it on disk, not in the chat.

### Phase 2 — Watch the draft (live)

**Context discipline (important):** over a multi-hour draft the main session
must stay lean so it never compacts. Enforce these rules:

- **Never run `draft.py poll` yourself in the main session.** Each poll,
  delegate to a subagent (Agent tool) that runs the command and returns only a
  compact digest. The raw Bash output stays in the subagent, not your context.
- **Keep the running pick log in `strategy.md` on disk**, not in the
  conversation. Your working memory in-chat should be one or two lines: whose
  turn, the user's roster shape, the current plan.
- When nothing relevant changed, say so in one line and move on — don't restate
  the board every tick.

**The poll loop.** To watch continuously, drive it with the `loop` skill, e.g.
the user runs `/loop 45s` and you follow this skill each tick; or you can pace
it yourself with `ScheduleWakeup` (~45s). Either way, each tick:

1. **Spawn a subagent** with a prompt like:
   > Run `python .claude/skills/espn-draft/scripts/draft.py poll` from the repo
   > root and return its output verbatim but trimmed to: picks_made + turn line,
   > any NEW PICKS, YOUR ROSTER / STILL NEED, and the top 5 available. If there
   > are no new picks and the user is >3 picks away, just reply "no change, N
   > picks away."

   The subagent absorbs the fetch; you get back a short digest.
2. **Integrate the delta**: if new picks change things (a run at a position, a
   target taken, a value falling), update `strategy.md` (append picks, adjust
   the plan). Use `Edit`, keep it terse.
3. **Advise only when it matters:**
   - **User on the clock or ≤2 picks away:** give a decisive recommendation —
     one clear pick, one backup, and 1–2 sentences of *why* (value + roster fit
     + scarcity/bye/injury). Don't bury it in a wall of text; they're on a clock.
   - **Otherwise:** a one-line status ("Pick 34, you're up in 6, plan holds —
     still targeting a WR here") and wait for the next tick.
4. **Re-arm** the next poll (loop handles this, or schedule the next wakeup).

Stop when the user says stop, or when the draft is complete (roster full /
picks_made = teams × rounds). Cancel any scheduled wakeups on stop.

## How to actually advise (you are the judge)

The tool's `poll`/`status` give you VOR, roster needs, and a scarcity flag —
treat those as strong *inputs*, not the answer. Layer on real reasoning:

- **Roster construction**: don't just take the highest VOR — balance starters,
  respect the user's plan in `strategy.md`, and avoid over-stacking a position.
- **Positional runs**: if several of a position just went (visible in NEW
  PICKS), the tier is thinning — pull that need forward.
- **Value vs. need**: early, lean best-player-by-VOR; mid/late, weight needs and
  upside; very late, fill K/DST and take upside darts.
- **Injuries / byes**: flag risky picks (the tool marks injury status); avoid
  clustering bye weeks at a position late.
- **Be decisive on the clock.** The user needs a pick, not a menu.

## On-demand (no loop)

If the user just asks "who should I take" or "where do things stand", delegate a
single `status` (or `poll --no-advance`) to a subagent and answer. Same context
rules apply — keep the raw output in the subagent.

## Quick reference

| Need | Command |
|------|---------|
| First-time config | `draft.py init` then edit `.espn-draft/config.json` |
| Find your team id | `draft.py teams` |
| Build/refresh board | `draft.py board [--pos RB] [--limit 40]` |
| Live delta (loop) | `draft.py poll` (via subagent) |
| Snapshot on demand | `draft.py status` (via subagent) |
| Re-report all picks | `draft.py reset` then `draft.py poll` |
