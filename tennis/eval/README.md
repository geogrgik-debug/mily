# `tennis.eval` — measuring the live state on history

Offline evaluation for track B: does updating the serve belief point by point
(`tennis.state`) beat the prior alone, and with what prior strength n0? The
answer, and what each number means, is in `tennis/state/README.md`. This file
covers the data, how to rerun it, and how the rows are built.

```bash
python -m tennis.eval download data/sackmann       # ATP 54 files, Slams 90, pointbypoint 8 (~320 MB)
python -m tennis.eval live-state --json data/eval/live_state.json    # about 6 minutes
```

Everything under `data/` is git-ignored: the files are CC BY-NC-SA 4.0,
research use only.

## Data

| Source | What | Where from |
|---|---|---|
| ATP match files | the prior, as of each tournament's start | `Aneeshers/tennis-sackmann-archive/atp` (`tennis.ratings download`) |
| Grand Slam point by point 2012–2024 | men's singles, a point per row with its server | the same mirror, `slam_pointbypoint`; 14 files are missing there: 2020 Wimbledon (not played) and AO and RG 2022–2024 |
| tennis_pointbypoint | ATP and Challenger, main draw and qualifying, 2011–2015 and 2017, a match per row as a string | `vinhonrubia/tennis_pointbypoint`, a fork: the original was deleted with Sackmann's other repositories |

## From files to rows

1. **Read** (`slam.py`, `pbp.py`) into one shape, `points.MatchPoints`: games
   in order, each point as (server, won). A game that does not end exactly on
   its last point, a serve that does not alternate, or a score column that
   disagrees with the points drops the whole match. Counts, 2026-09-23:
   - Slams: 4 699 men's matches kept; 173 with a game missing or adding a
     point, 3 with the serve out of order.
   - tennis_pointbypoint: 46 944 kept; 98 repeated ids, 16 unparseable
     games, 1 winner that disagrees with the points.
2. **Join** (`join.py`) each match to its Sackmann row by the pair of names,
   first initial plus surname, then any surname word if that finds nothing.
   Slams look inside their own event. tennis_pointbypoint uses a window of 16
   days before to 4 days after the match date, and takes the event that
   started last.
   - Slams: 4 682 joined (60 by the loose test), 17 not found.
   - tennis_pointbypoint: 36 999 joined (604 loose); 9 630 not found, 4 963
     of them Challenger qualifying before 2017, which Sackmann's files do not
     carry; 278 duplicates, 37 ambiguous.
   A sample of 25 loose joins was checked by hand: all right ('Victor
   Estrella Burgos' is 'Victor Estrella', 'Alex Jr. Bogomolov' is 'Alex
   Bogomolov Jr').
3. **Price** each match with `RatingsSnapshot.prior` as of its tournament's
   start date, from every tournament that started strictly earlier.
   `stream_priors` replays the files once. It is tested equal to a snapshot
   rebuilt as of each date.
4. **Rows** (`live_state.build`), one per regular service game:
   - the server's prior;
   - his serve points won and lost so far, tie-breaks included;
   - the same for the returner;
   - whether he held.

   A game's points are folded in only after its row is out. Tie-breaks feed
   the counts but are not forecast.

## What is measured, per segment

The segments are Slams, tour and Challenger. Training is 2012–2018 for Slams
(experiment B2's split) and 2011–2015 for tennis_pointbypoint; the test is
2019–2024 and 2017.

- **n0**: the grid value with the lowest hold log loss on the training games
  where the server has served before, plus a 95 % bootstrap range by match.
  It is checked against the likelihood of single serve points.
- **Split-half** (audit, appendix A.5): the slope of the serve deviation over
  the rest of a match on the deviation over its first k service games.
- **Serve link**: the correlation of the two servers' true deviations from
  their priors, net of binomial noise. It is the part of the audit's four-way
  state that points can see.
- **On the test years**:
  - log loss of the prior alone and of the live state, and the gain with a
    95 % interval by resampling whole matches;
  - the same after recalibrating both models on the training years (Platt);
  - the swap alarm: the server's belief fed his opponent's serve points must
    do worse than the prior;
  - averaging over the posterior against plugging in its mean;
  - the fitted n0 against game_rows' old 40;
  - the gain by how many rated matches stand behind the server's Elo;
  - calibration by deciles.

## Tests

`tests/test_sources.py` covers the readers, the joins and the priors.

`tests/test_live_state.py` runs the machinery on simulated matches where the
truth is known. Each player's form is his prior plus a draw of known spread.
The point likelihood must recover n0 = p(1−p)/sd² − 1. The split-half slope
must equal n/(n+n0). The serve link must find the correlation put in: 0, −0.6
or +0.6. The rows must survive poisoning of later games and equal
`MatchState` folded over the same points.
