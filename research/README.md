# research/

Reproducible calculations behind the audit and the follow-up experiments.

| File | What |
|---|---|
| `calc.py` | Markov hold/break probabilities by score, exact-score tree, margin analysis of the blueprint screenshot, standard errors, i.i.d. null model for "hot state" patterns, likelihood-ratio and power simulations. |
| `mcp_next_game_study.py` | Service-game dataset from the Match Charting Project; chronological ablation of feature groups; split-half "day form" test; cluster bootstrap by match. |
| `elo_prior.py` | Surface-blended Elo, Markov match model (game, tiebreak, set, match), inversion of a match win probability into a pair of serve-point-win probabilities, Barnett-Clarke opponent adjustment, validation by serve-points-won forecast RMSE. |
| `elo_sweep.py` | Sweeps the Elo K-shape and surface weight to test whether the Elo-vs-Barnett-Clarke result is an artefact of an under-tuned rating. |
| `slam_process_study.py` | Service-game dataset from Grand Slam point-by-point with Hawk-Eye process fields (serve speed, rally length, return depth, distance run); feature-group ablation for men, women and the tracked subset. |
| `confound_test.py` | Removes the prior from the men's games to test whether process features carry today's state or just player identity. |
| `*_results.json` | Outputs as run on 2026-09-21. |

## Data

Not redistributed here. All of it is CC BY-NC-SA 4.0 — non-commercial research use only.

- Match Charting Project: `JeffSackmann/tennis_MatchChartingProject` (still public).
- ATP matches and Grand Slam point-by-point: the four original Sackmann data repos were removed
  from GitHub between June and July 2026; the mirror `Aneeshers/tennis-sackmann-archive` carries
  ATP/WTA through June 2026 and slam point-by-point 2011-2024.

Download commands are in `docs/EXPERIMENT_B_elo_prior_and_process.md`.

## Order to run

```bash
pip install numpy scipy pandas scikit-learn
python calc.py
python elo_prior.py atp elo_prior_results.json priors.csv
python slam_process_study.py slam priors.csv atp slam_process_results.json
python confound_test.py
```
