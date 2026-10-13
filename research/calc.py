import numpy as np
from functools import lru_cache
from math import comb, sqrt

# ---------- 1. Markov game model ----------
def p_hold_from(p, a, b):
    """P(server wins game) from score a (server pts) - b (returner pts), iid point prob p."""
    q = 1-p
    @lru_cache(None)
    def f(a,b):
        if a>=4 and a-b>=2: return 1.0
        if b>=4 and b-a>=2: return 0.0
        if a>=3 and b>=3:
            if a==b: return p*p/(p*p+q*q)
            if a==b+1: return p + q*p*p/(p*p+q*q)
            if b==a+1: return p*p*p/(p*p+q*q)  # p * deuce
        return p*f(a+1,b)+q*f(a,b+1)
    return f(a,b)

def exact_score_dist(p):
    """distribution over 8 outcomes: hold 40-0,15,30,'40'(deuce route) and break at 0,15,30,'40'."""
    q=1-p
    d={}
    d['hold_0']=p**4; d['hold_15']=4*p**4*q; d['hold_30']=10*p**4*q**2
    d['break_0']=q**4; d['break_15']=4*q**4*p; d['break_30']=10*q**4*p**2
    deuce=20*p**3*q**3
    d['hold_40']=deuce*p*p/(p*p+q*q); d['break_40']=deuce*q*q/(p*p+q*q)
    return d

print("=== Hold probability by point-win prob p and current score ===")
print("p     P(hold|0-0) P(hold|0-15) P(hold|0-30) P(hold|15-0) P(hold|30-0) P(hold|0-40)")
for p in [0.55,0.60,0.62,0.65,0.70,0.75]:
    print(f"{p:.2f}  {p_hold_from(p,0,0):.3f}       {p_hold_from(p,0,1):.3f}        {p_hold_from(p,0,2):.3f}        {p_hold_from(p,1,0):.3f}        {p_hold_from(p,2,0):.3f}        {p_hold_from(p,0,3):.3f}")

print("\n=== dP(hold)/dp (sensitivity) ===")
for p in [0.55,0.60,0.65,0.70]:
    h=1e-4; print(f"p={p}: dH/dp = {(p_hold_from(p+h,0,0)-p_hold_from(p-h,0,0))/(2*h):.2f}")

print("\n=== Break odds example: 4.20 -> 3.10 -> 1.85 ===")
for o in [4.20,3.10,1.85]:
    print(f"odds {o}: implied {1/o:.3f}")
# find p such that P(break|0-0)=1/4.2 ~ 0.238 (raw, ignoring margin) and see mechanical update
from scipy.optimize import brentq
for target in [0.238, 0.215]:  # raw and ~10% margin-removed
    p=brentq(lambda p: 1-p_hold_from(p,0,0)-target, 0.5,0.9)
    print(f"target P(break|0-0)={target:.3f} -> p={p:.3f}; mechanical P(break|0-15)={1-p_hold_from(p,0,1):.3f}, P(break|0-30)={1-p_hold_from(p,0,2):.3f}")

print("\n=== Exact score distribution ===")
for p in [0.60,0.65,0.70]:
    d=exact_score_dist(p); print(p, {k:round(v,3) for k,v in d.items()}, 'sum',round(sum(d.values()),4))

# ---------- 2. Screenshot market (set 2 game 9, server = P2) ----------
print("\n=== Screenshot exact-score market ===")
odds={'P1:0(break@0)':27.0,'P1:15(break@15)':11.5,'P1:30(break@30)':8.0,'P1:40(break@40)':8.5,
      '0:P2(hold 40-0)':6.5,'15:P2(hold 40-15)':4.35,'30:P2(hold 40-30)':4.2,'40:P2(hold deuce)':4.75}
raw={k:1/v for k,v in odds.items()}; ov=sum(raw.values())
print(f"overround = {ov:.3f} ({(ov-1)*100:.1f}% margin)")
fair={k:v/ov for k,v in raw.items()}
for k in odds: print(f"  {k:22s} odds {odds[k]:5.2f} raw {raw[k]:.3f} fair {fair[k]:.3f}")
ph=sum(v for k,v in fair.items() if 'P2' in k); print(f"implied P(hold) fair = {ph:.3f}, P(break)= {1-ph:.3f}")
# fit p
def fit(p):
    d=exact_score_dist(p)
    m={'P1:0(break@0)':d['break_0'],'P1:15(break@15)':d['break_15'],'P1:30(break@30)':d['break_30'],'P1:40(break@40)':d['break_40'],
       '0:P2(hold 40-0)':d['hold_0'],'15:P2(hold 40-15)':d['hold_15'],'30:P2(hold 40-30)':d['hold_30'],'40:P2(hold deuce)':d['hold_40']}
    return sum((m[k]-fair[k])**2 for k in fair), m
best=min(np.linspace(0.5,0.8,301), key=lambda p: fit(p)[0]); m=fit(best)[1]
print(f"best-fit iid p = {best:.3f}; model dist:", {k:round(v,3) for k,v in m.items()})
print("residuals (fair - model):", {k:round(fair[k]-m[k],3) for k in fair})
# total points market
tp={'4':5.4,'5':3.3,'6':2.9,'7+':3.2}; r={k:1/v for k,v in tp.items()}; print("total points overround:",round(sum(r.values()),3))

# ---------- 3. Sample size ----------
print("\n=== Standard error of hold% estimate ===")
for h in [0.80]:
    for n in [5,10,20,50,100,200,500]:
        print(f"hold={h}, n games={n}: SE={sqrt(h*(1-h)/n):.3f}")
print("SE of serve-point-win p:")
for p in [0.63]:
    for n in [20,40,60,100,300,1000]:
        print(f"p={p}, n points={n}: SE={sqrt(p*(1-p)/n):.3f}  -> implied SE on P(hold) ~ {sqrt(p*(1-p)/n)*2.6:.3f}")

# ---------- 4. Null model for 'hot state' ----------
print("\n=== Null model: iid server p=0.64 (hold ~0.80). Frequencies of 'hot-looking' patterns per match (12 service games) ===")
rng=np.random.default_rng(0)
def sim_game(p):
    a=b=0
    while True:
        if rng.random()<p: a+=1
        else: b+=1
        if a>=4 and a-b>=2: return 'hold', b
        if b>=4 and b-a>=2: return 'break', a
N=200000; p=0.64
cnt3easy=0; cnt3any=0; cnt_5hold=0; games_per=12
for _ in range(N):
    res=[sim_game(p) for _ in range(games_per)]
    # 3 consecutive holds conceding <=1 point
    easy=[r=='hold' and b<=1 for r,b in res]
    if any(all(easy[i:i+3]) for i in range(games_per-2)): cnt3easy+=1
    holds=[r=='hold' for r,b in res]
    if any(all(holds[i:i+5]) for i in range(games_per-4)): cnt_5hold+=1
print(f"P(at least one run of 3 consecutive 'easy holds' (<=1 pt lost) in 12 service games) = {cnt3easy/N:.3f}")
print(f"P(at least one run of 5 consecutive holds) = {cnt_5hold/N:.3f}")
# probability that a genuinely 'hot' p=0.70 player is distinguishable after k games
print("\nLikelihood ratio for p=0.70 vs p=0.64 after observing k service games' point totals (expected LR):")
for k in [2,4,6,8]:
    lrs=[]
    for _ in range(20000):
        w=0;l=0
        for _ in range(k):
            r,x=sim_game(0.70); 
            if r=='hold': w+=4 if x<3 else 5; l+=x if x<3 else x  # approx pts
            else: w+=x; l+=4 if x<3 else 5
        ll=w*np.log(0.70/0.64)+l*np.log(0.30/0.36); lrs.append(ll)
    lrs=np.array(lrs); print(f" k={k}: median log-LR={np.median(lrs):.2f}, P(logLR>1.1 i.e. LR>3)={np.mean(lrs>1.1):.2f}")

# ---------- 5. Power for paper trading: model vs market Brier ----------
print("\n=== Power: how many games to show model beats market in log-loss ===")
# assume true prob t ~ market fair prob + noise; market has error sd_m, model error sd_model
def power(n, sd_market=0.06, sd_model=0.05, reps=2000, base=0.80):
    wins=0
    for _ in range(reps):
        t=np.clip(rng.normal(base,0.10,n),0.3,0.97)
        y=rng.random(n)<t
        pm=np.clip(t+rng.normal(0,sd_market,n),0.02,0.98)
        pk=np.clip(t+rng.normal(0,sd_model,n),0.02,0.98)
        ll_m=-(y*np.log(pm)+(~y)*np.log(1-pm)); ll_k=-(y*np.log(pk)+(~y)*np.log(1-pk))
        d=ll_m-ll_k; se=d.std(ddof=1)/sqrt(n)
        if d.mean()/se>1.645: wins+=1
    return wins/reps
for n in [500,1000,2000,5000,10000,20000]:
    print(f"n={n}: power(model sd .05 vs market sd .06)={power(n):.2f}   power(.04 vs .06)={power(n,0.06,0.04):.2f}")
