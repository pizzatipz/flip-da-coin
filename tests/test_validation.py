"""Quick validation test for the analysis and strategy modules."""
import sys
sys.path.insert(0, ".")

from pathlib import Path
import random
from src.db import get_connection, init_db, insert_flip

TEST_DB = Path("data/test_validation.db")
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
if TEST_DB.exists():
    TEST_DB.unlink()

conn = get_connection(TEST_DB)
init_db(conn)

# Insert 500 synthetic flips: ~49% H, ~49% T, ~2% M
random.seed(42)
for _ in range(500):
    r = random.random()
    if r < 0.02:
        o = "MIDDLE"
    elif r < 0.51:
        o = "HEADS"
    else:
        o = "TAILS"
    insert_flip(conn, o)

from src.analyze import (
    load_flips, descriptive_stats, chi_squared_test, runs_test,
    autocorrelation_analysis, transition_matrix_analysis,
    streak_analysis, fft_periodicity, conditional_probability_analysis,
)
from src.strategies import backtest_all

df = load_flips(conn)
print(f"Loaded {len(df)} flips")

desc = descriptive_stats(df)
print("OK: descriptive_stats")
print(f"  House edge: {desc['house_edge_pct']}%")
for out in ["HEADS", "TAILS", "MIDDLE"]:
    d = desc["outcomes"][out]
    print(f"  {out}: {d['pct']:.2f}% [{d['ci_95_low_pct']:.2f}%, {d['ci_95_high_pct']:.2f}%]")

chi2 = chi_squared_test(df)
print("OK: chi_squared_test")
print(f"  H/T symmetry p={chi2['heads_tails_symmetry']['p_value']}")

runs = runs_test(df)
print("OK: runs_test")

acf = autocorrelation_analysis(df)
print("OK: autocorrelation")
print(f"  Ljung-Box p={acf['ljung_box']['p_value']}")
print(f"  Significant lags: {acf['significant_lags']}")

trans = transition_matrix_analysis(df)
print("OK: transition_matrix")
print(f"  Independence p={trans['independence_test']['p_value']}")

streaks = streak_analysis(df)
print("OK: streak_analysis")

fft = fft_periodicity(df)
print("OK: fft_periodicity")
print(f"  Fisher p={fft['fisher_test']['p_value']}")

cond = conditional_probability_analysis(df)
print("OK: conditional_probability")
print(f"  Contexts analyzed: {len(cond.get('conditional_after_streaks', {}))}")

strats = backtest_all(df)
print(f"OK: backtest_all ({len(strats)} strategies)")
for name, data in sorted(strats.items(), key=lambda x: x[1].get("roi_pct", -999), reverse=True):
    if isinstance(data, dict) and "roi_pct" in data:
        print(f"  {name:<25s} ROI={data['roi_pct']:7.2f}%  Bets={data['n_bets']}")

conn.close()
TEST_DB.unlink()
print("\n=== ALL TESTS PASSED ===")
