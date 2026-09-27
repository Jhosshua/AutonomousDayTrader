import pandas as pd, numpy as np, sys
df = pd.read_csv(sys.argv[1] if len(sys.argv)>1 else "data/baseline.csv")
df["year"]=df.date.str[:4]; df["period"]=np.where(df.date<"2025-04-01","A_IS",np.where(df.date<"2026-01-01","B_VAL","C_HOLD"))
df["window"]=np.where(df.entry_m<690,"morning","afternoon")
df["hour"]=(df.entry_m//60)
df["dow"]=pd.to_datetime(df.date).dt.dayofweek
def cut(col, bins=None, q=None):
    x=df.copy()
    if q: x[col+"_q"]=pd.qcut(x[col],q,duplicates="drop"); col=col+"_q"
    elif bins is not None: x[col+"_b"]=pd.cut(x[col],bins); col=col+"_b"
    g=x.groupby(col,observed=True).agg(n=("r","size"),mean_r=("r","mean"),se=("r",lambda s: s.std(ddof=1)/np.sqrt(len(s))),pnl=("pnl","sum"),win=("pnl",lambda s:(s>0).mean()),t1=("t1_hit","mean"))
    print(f"\n== {col}"); print(g.round(3).to_string())
print("overall", len(df), round(df.r.mean(),4), round(df.pnl.sum()))
for c in ["period","year","side","sym","window","hour","dow","exit_reason","vix_regime","trend","t1_hit","fb1","fb2","floored","cap_hit","tested","prior_zone","wick","volc"]:
    cut(c)
for c,q in [("stop_pct",5),("std_pct",5),("volume_ratio",5),("wick_ratio",5),("ema_gap_pct",5),("close_vs_vwap_pct",5),("bars_seen",4),("hold_min",5),("mfe_r",5),("mae_r",5),("notional",4),("vix",5)]:
    cut(c,q=q)
print("\n== reward geometry (T1 distance in R)")
df["t1_r"]=np.where(df.side=="LONG",(df.tp1-df.entry),(df.entry-df.tp1))/df.risk
df["t2_r"]=np.where(df.side=="LONG",(df.tp2-df.entry),(df.entry-df.tp2))/df.risk
print(df[["t1_r","t2_r"]].describe().round(2).to_string())
cut("t1_r",q=5)
print("\n== mfe by outcome"); print(df.groupby("exit_reason")[["mfe_r","mae_r","hold_min"]].describe().round(2).to_string())
print("\n== how much of the loss is the stop-after-T1 (breakeven scratch) path?")
print(df.groupby(["t1_hit","exit_reason"]).agg(n=("r","size"),mean_r=("r","mean"),pnl=("pnl","sum")).round(3).to_string())
