# research/

Reproducible calculations behind `docs/AUDIT_Tennis_Live_Next_Game_v1.md`.

| File | What |
|---|---|
| `calc.py` | Markov hold/break probabilities by score, exact-score tree, market-margin analysis of the blueprint screenshot, standard errors, i.i.d. null model for "hot state" patterns, likelihood-ratio and power simulations (Appendix C). `pip install numpy scipy && python calc.py` |
| `mcp_next_game_study.py` | Empirical mini-study on the Tennis Abstract Match Charting Project (men): builds a "one row = one service game" dataset, chronological train/test (split 2023-01-01), nested ablation of feature groups (player prior / in-match cumulative / previous-game outcome / previous-game process / score context), prior-window comparison, conditional hold rates, split-half "day-form" test, cluster bootstrap by match (Appendix A). |
| `mcp_study_results.json` | Output of the study as run on 2026-09-21 (MCP snapshot of 2026-09-18). |

## Running the MCP study

Data (CC BY-NC-SA 4.0, non-commercial research use only; not redistributed here):

```
mkdir mcp && cd mcp
for f in charting-m-matches.csv charting-m-points-2010s.csv charting-m-points-2020s.csv; do
  curl -sSL -O https://raw.githubusercontent.com/JeffSackmann/tennis_MatchChartingProject/master/$f
done
cd .. && pip install pandas numpy scikit-learn && python mcp_next_game_study.py mcp results.json
```

First run parses ~1.1M points (~3 min) and caches `mcp/_games_cache.pkl`; subsequent runs take ~3 min (feature building + bootstraps).
