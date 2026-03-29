"""
Statistical analysis pipeline for Flip Da' Coin outcomes.

Full pipeline:
  1. Descriptive statistics with confidence intervals
  2. Chi-squared goodness-of-fit tests
  3. Runs test for randomness
  4. Autocorrelation analysis (lags 1–20)
  5. Transition matrix analysis
  6. Streak distribution analysis (vs geometric expectation)
  7. FFT periodicity detection
  8. Conditional probability analysis
  9. Time-of-day analysis
  10. Strategy backtesting summary
"""

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from src.db import get_connection, init_db

REPORT_DIR = Path(__file__).parent.parent / "reports"


# ---------------------------------------------------------------------------
# 1. DATA LOADING
# ---------------------------------------------------------------------------

def load_flips(conn) -> pd.DataFrame:
    """Load all flip records into a DataFrame."""
    df = pd.read_sql_query(
        "SELECT id, round_id, timestamp, outcome, scraped_at FROM flips ORDER BY id",
        conn,
    )
    if df.empty:
        return df

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["scraped_at"] = pd.to_datetime(df["scraped_at"], utc=True)
    return df


# ---------------------------------------------------------------------------
# 2. DESCRIPTIVE STATISTICS
# ---------------------------------------------------------------------------

def descriptive_stats(df: pd.DataFrame) -> dict:
    """
    Compute counts, proportions, and Wilson score confidence intervals
    for each outcome.

    Wilson CI is used instead of the normal approximation because it
    performs well even at small sample sizes and extreme proportions
    (important for MIDDLE which may be ~2%).
    """
    n = len(df)
    counts = df["outcome"].value_counts()

    results = {"n": n, "outcomes": {}}

    for outcome in ["HEADS", "TAILS", "MIDDLE"]:
        k = int(counts.get(outcome, 0))
        p_hat = k / n if n > 0 else 0

        # Wilson score interval (95% CI)
        z = 1.96  # 95% confidence
        denom = 1 + z**2 / n
        center = (p_hat + z**2 / (2 * n)) / denom
        margin = z * np.sqrt((p_hat * (1 - p_hat) + z**2 / (4 * n)) / n) / denom

        ci_low = max(0, center - margin)
        ci_high = min(1, center + margin)

        results["outcomes"][outcome] = {
            "count": k,
            "proportion": round(p_hat, 6),
            "pct": round(p_hat * 100, 4),
            "ci_95_low": round(ci_low, 6),
            "ci_95_high": round(ci_high, 6),
            "ci_95_low_pct": round(ci_low * 100, 4),
            "ci_95_high_pct": round(ci_high * 100, 4),
        }

    # Derived metrics
    h = results["outcomes"]["HEADS"]["proportion"]
    t = results["outcomes"]["TAILS"]["proportion"]
    m = results["outcomes"]["MIDDLE"]["proportion"]

    results["house_edge_pct"] = round(m * 100, 4)
    results["ev_per_unit_bet"] = round(
        max(h, t) * 2 - 1, 6
    )  # EV if you always bet the more frequent side

    # Symmetry: ratio of HEADS to (HEADS+TAILS)
    ht_total = h + t
    if ht_total > 0:
        h_given_not_middle = h / ht_total
        results["p_heads_given_not_middle"] = round(h_given_not_middle, 6)
    else:
        results["p_heads_given_not_middle"] = None

    return results


# ---------------------------------------------------------------------------
# 3. CHI-SQUARED GOODNESS-OF-FIT
# ---------------------------------------------------------------------------

def chi_squared_test(df: pd.DataFrame, expected_middle: float = None) -> dict:
    """
    Test whether the observed distribution matches expected proportions.

    If expected_middle is None, we test against a model where
    P(HEADS) = P(TAILS) and P(MIDDLE) = observed P(MIDDLE).
    This isolates the HEADS/TAILS symmetry question.

    We also test the full null hypothesis: equal H/T with various
    MIDDLE rates.
    """
    n = len(df)
    counts = df["outcome"].value_counts()
    observed = np.array([
        counts.get("HEADS", 0),
        counts.get("TAILS", 0),
        counts.get("MIDDLE", 0),
    ], dtype=float)

    results = {}

    # Test 1: Are HEADS and TAILS equally likely (excluding MIDDLE)?
    ht_obs = np.array([counts.get("HEADS", 0), counts.get("TAILS", 0)], dtype=float)
    ht_total = ht_obs.sum()
    if ht_total > 0:
        ht_expected = np.array([ht_total / 2, ht_total / 2])
        chi2, p_val = sp_stats.chisquare(ht_obs, ht_expected)
        results["heads_tails_symmetry"] = {
            "test": "chi-squared goodness-of-fit (H vs T only)",
            "observed_heads": int(ht_obs[0]),
            "observed_tails": int(ht_obs[1]),
            "expected_each": round(ht_total / 2, 2),
            "chi2_statistic": round(float(chi2), 6),
            "p_value": round(float(p_val), 8),
            "significant_at_005": p_val < 0.05,
            "interpretation": (
                "HEADS and TAILS frequencies are significantly different (reject symmetry)"
                if p_val < 0.05
                else "No significant difference between HEADS and TAILS (consistent with fair)"
            ),
        }

    # Test 2: Full distribution against a hypothesized model
    # Use observed MIDDLE rate as the null hypothesis parameter
    obs_middle_rate = observed[2] / n
    if expected_middle is not None:
        test_middle = expected_middle
    else:
        test_middle = obs_middle_rate

    expected_full = np.array([
        n * (1 - test_middle) / 2,
        n * (1 - test_middle) / 2,
        n * test_middle,
    ])
    # Only run if all expected counts >= 5 (chi-squared requirement)
    if np.all(expected_full >= 5):
        chi2, p_val = sp_stats.chisquare(observed, expected_full)
        results["full_distribution"] = {
            "test": "chi-squared goodness-of-fit (H/T/M)",
            "assumed_middle_rate": round(test_middle, 6),
            "observed": {"HEADS": int(observed[0]), "TAILS": int(observed[1]), "MIDDLE": int(observed[2])},
            "expected": {
                "HEADS": round(expected_full[0], 2),
                "TAILS": round(expected_full[1], 2),
                "MIDDLE": round(expected_full[2], 2),
            },
            "chi2_statistic": round(float(chi2), 6),
            "p_value": round(float(p_val), 8),
            "significant_at_005": p_val < 0.05,
        }
    else:
        results["full_distribution"] = {
            "test": "chi-squared (skipped — expected counts too low)",
            "note": f"Need more data. MIDDLE expected count = {expected_full[2]:.1f} (need >= 5)",
        }

    # Test 3: Binomial test for MIDDLE rate against common house edges
    for test_rate in [0.01, 0.02, 0.03, 0.05]:
        label = f"binomial_middle_{int(test_rate*100)}pct"
        middle_count = int(observed[2])
        binom_result = sp_stats.binomtest(middle_count, n, test_rate)
        results[label] = {
            "test": f"Binomial test: P(MIDDLE) = {test_rate}",
            "observed_middle": middle_count,
            "observed_rate": round(middle_count / n, 6),
            "hypothesized_rate": test_rate,
            "p_value": round(float(binom_result.pvalue), 8),
            "ci_95": [round(binom_result.proportion_ci(0.05).low, 6),
                      round(binom_result.proportion_ci(0.05).high, 6)],
            "significant_at_005": binom_result.pvalue < 0.05,
        }

    return results


# ---------------------------------------------------------------------------
# 4. RUNS TEST
# ---------------------------------------------------------------------------

def runs_test(df: pd.DataFrame) -> dict:
    """
    Wald-Wolfowitz runs test to check if the sequence of outcomes
    is random.

    A "run" is a consecutive sequence of the same outcome.
    If the RNG is fair, the number of runs should match the expected
    value from a random sequence.

    We test:
    (a) H vs not-H (binary)
    (b) T vs not-T (binary)
    (c) H vs T (excluding MIDDLE entirely)
    """
    outcomes = df["outcome"].values
    results = {}

    def _count_runs(binary_seq):
        """Count the number of runs in a binary (0/1) sequence."""
        if len(binary_seq) < 2:
            return len(binary_seq), 0, 0
        runs = 1
        for i in range(1, len(binary_seq)):
            if binary_seq[i] != binary_seq[i - 1]:
                runs += 1
        n1 = int(np.sum(binary_seq == 1))
        n0 = int(np.sum(binary_seq == 0))
        return runs, n1, n0

    def _runs_z_test(runs, n1, n0):
        """Compute z-statistic for the runs test."""
        n = n1 + n0
        if n1 == 0 or n0 == 0 or n < 2:
            return None, None

        expected_runs = (2 * n1 * n0) / n + 1
        var_runs = (2 * n1 * n0 * (2 * n1 * n0 - n)) / (n**2 * (n - 1))

        if var_runs <= 0:
            return None, None

        z = (runs - expected_runs) / np.sqrt(var_runs)
        p_value = 2 * (1 - sp_stats.norm.cdf(abs(z)))  # two-tailed
        return z, p_value

    # (a) H vs not-H
    binary_h = (outcomes == "HEADS").astype(int)
    runs_h, n1_h, n0_h = _count_runs(binary_h)
    z_h, p_h = _runs_z_test(runs_h, n1_h, n0_h)
    results["heads_vs_rest"] = {
        "test": "Wald-Wolfowitz runs test (HEADS vs not-HEADS)",
        "n_runs": runs_h,
        "n_heads": n1_h,
        "n_other": n0_h,
        "z_statistic": round(float(z_h), 6) if z_h is not None else None,
        "p_value": round(float(p_h), 8) if p_h is not None else None,
        "significant_at_005": p_h < 0.05 if p_h is not None else None,
    }

    # (b) T vs not-T
    binary_t = (outcomes == "TAILS").astype(int)
    runs_t, n1_t, n0_t = _count_runs(binary_t)
    z_t, p_t = _runs_z_test(runs_t, n1_t, n0_t)
    results["tails_vs_rest"] = {
        "test": "Wald-Wolfowitz runs test (TAILS vs not-TAILS)",
        "n_runs": runs_t,
        "n_tails": n1_t,
        "n_other": n0_t,
        "z_statistic": round(float(z_t), 6) if z_t is not None else None,
        "p_value": round(float(p_t), 8) if p_t is not None else None,
        "significant_at_005": p_t < 0.05 if p_t is not None else None,
    }

    # (c) H vs T (excluding MIDDLE)
    ht_mask = (outcomes == "HEADS") | (outcomes == "TAILS")
    ht_outcomes = outcomes[ht_mask]
    binary_ht = (ht_outcomes == "HEADS").astype(int)
    runs_ht, n1_ht, n0_ht = _count_runs(binary_ht)
    z_ht, p_ht = _runs_z_test(runs_ht, n1_ht, n0_ht)
    results["heads_vs_tails_only"] = {
        "test": "Wald-Wolfowitz runs test (HEADS vs TAILS, MIDDLE excluded)",
        "n_runs": runs_ht,
        "n_heads": n1_ht,
        "n_tails": n0_ht,
        "z_statistic": round(float(z_ht), 6) if z_ht is not None else None,
        "p_value": round(float(p_ht), 8) if p_ht is not None else None,
        "significant_at_005": p_ht < 0.05 if p_ht is not None else None,
    }

    return results


# ---------------------------------------------------------------------------
# 5. AUTOCORRELATION ANALYSIS
# ---------------------------------------------------------------------------

def autocorrelation_analysis(df: pd.DataFrame, max_lag: int = 20) -> dict:
    """
    Compute autocorrelation of the outcome sequence at lags 1 through max_lag.

    We encode outcomes as: HEADS=1, TAILS=-1, MIDDLE=0
    Then compute the standard autocorrelation function.

    For a truly random sequence, autocorrelation at any lag should be
    approximately 0 within the bounds ±1.96/√n.
    """
    outcomes = df["outcome"].values
    # Numerical encoding
    encoding = {"HEADS": 1, "TAILS": -1, "MIDDLE": 0}
    seq = np.array([encoding[o] for o in outcomes], dtype=float)

    n = len(seq)
    mean = np.mean(seq)
    var = np.var(seq)

    if var == 0 or n < max_lag + 1:
        return {"error": "Insufficient data or zero variance"}

    # Compute autocorrelation at each lag
    autocorrelations = {}
    ci_bound = 1.96 / np.sqrt(n)  # 95% CI for white noise

    significant_lags = []
    for lag in range(1, min(max_lag + 1, n)):
        # Standard autocorrelation formula
        cov = np.mean((seq[:n - lag] - mean) * (seq[lag:] - mean))
        acf_val = cov / var

        is_significant = abs(acf_val) > ci_bound
        autocorrelations[f"lag_{lag}"] = {
            "autocorrelation": round(float(acf_val), 6),
            "significant": is_significant,
        }
        if is_significant:
            significant_lags.append(lag)

    results = {
        "encoding": "HEADS=1, TAILS=-1, MIDDLE=0",
        "n": n,
        "ci_95_bound": round(float(ci_bound), 6),
        "autocorrelations": autocorrelations,
        "significant_lags": significant_lags,
        "any_significant": len(significant_lags) > 0,
        "interpretation": (
            f"Significant autocorrelation found at lags {significant_lags} — "
            "outcomes may not be fully independent."
            if significant_lags
            else "No significant autocorrelation at any lag — consistent with independence."
        ),
    }

    # Ljung-Box test (portmanteau test for overall autocorrelation)
    acf_values = [autocorrelations[f"lag_{lag}"]["autocorrelation"]
                  for lag in range(1, min(max_lag + 1, n))]
    Q = n * (n + 2) * sum(r**2 / (n - k) for k, r in enumerate(acf_values, 1))
    lb_p_value = 1 - sp_stats.chi2.cdf(Q, df=len(acf_values))
    results["ljung_box"] = {
        "test": "Ljung-Box portmanteau test",
        "Q_statistic": round(float(Q), 6),
        "df": len(acf_values),
        "p_value": round(float(lb_p_value), 8),
        "significant_at_005": lb_p_value < 0.05,
        "interpretation": (
            "Overall autocorrelation is significant — sequence is NOT white noise."
            if lb_p_value < 0.05
            else "No significant overall autocorrelation — consistent with white noise."
        ),
    }

    return results


# ---------------------------------------------------------------------------
# 6. TRANSITION MATRIX
# ---------------------------------------------------------------------------

def transition_matrix_analysis(df: pd.DataFrame) -> dict:
    """
    Build and analyze the transition matrix: P(outcome_t | outcome_{t-1}).

    If outcomes are independent, the transition probabilities should
    equal the marginal probabilities (i.e., knowing the previous outcome
    shouldn't change the probability of the next one).

    We test this with a chi-squared test of independence.
    """
    outcomes = df["outcome"].values
    labels = ["HEADS", "TAILS", "MIDDLE"]
    n_states = len(labels)
    label_to_idx = {l: i for i, l in enumerate(labels)}

    # Build count matrix
    trans_counts = np.zeros((n_states, n_states), dtype=int)
    for i in range(len(outcomes) - 1):
        from_idx = label_to_idx[outcomes[i]]
        to_idx = label_to_idx[outcomes[i + 1]]
        trans_counts[from_idx][to_idx] += 1

    # Compute probabilities
    row_sums = trans_counts.sum(axis=1, keepdims=True)
    # Avoid division by zero
    with np.errstate(divide="ignore", invalid="ignore"):
        trans_probs = np.where(row_sums > 0, trans_counts / row_sums, 0)

    # Format as readable dict
    matrix_counts = {}
    matrix_probs = {}
    for i, from_label in enumerate(labels):
        matrix_counts[from_label] = {
            to_label: int(trans_counts[i][j]) for j, to_label in enumerate(labels)
        }
        matrix_probs[from_label] = {
            to_label: round(float(trans_probs[i][j]), 6) for j, to_label in enumerate(labels)
        }

    # Chi-squared test of independence on the transition table
    # (tests whether next outcome is independent of previous outcome)
    # Only use rows/columns with sufficient data
    if np.all(row_sums > 0):
        chi2, p_value, dof, expected = sp_stats.chi2_contingency(trans_counts)
        independence_test = {
            "test": "chi-squared test of independence on transition matrix",
            "chi2_statistic": round(float(chi2), 6),
            "degrees_of_freedom": int(dof),
            "p_value": round(float(p_value), 8),
            "significant_at_005": p_value < 0.05,
            "interpretation": (
                "Transition probabilities depend on previous outcome — NOT independent."
                if p_value < 0.05
                else "No evidence of dependence — transitions consistent with independence."
            ),
        }
    else:
        independence_test = {
            "test": "chi-squared (skipped — insufficient data for some transitions)",
        }

    # Check specific conditional probabilities of interest
    marginal = df["outcome"].value_counts(normalize=True)
    conditional_analysis = {}
    for from_label in labels:
        row_total = int(row_sums[label_to_idx[from_label], 0])
        if row_total < 10:
            continue
        for to_label in labels:
            p_cond = trans_probs[label_to_idx[from_label]][label_to_idx[to_label]]
            p_marginal = marginal.get(to_label, 0)
            diff = p_cond - p_marginal
            key = f"P({to_label}|prev={from_label})"
            conditional_analysis[key] = {
                "conditional": round(float(p_cond), 6),
                "marginal": round(float(p_marginal), 6),
                "difference": round(float(diff), 6),
                "n_transitions": int(trans_counts[label_to_idx[from_label]][label_to_idx[to_label]]),
            }

    return {
        "transition_counts": matrix_counts,
        "transition_probabilities": matrix_probs,
        "independence_test": independence_test,
        "conditional_vs_marginal": conditional_analysis,
    }


# ---------------------------------------------------------------------------
# 7. STREAK ANALYSIS
# ---------------------------------------------------------------------------

def streak_analysis(df: pd.DataFrame) -> dict:
    """
    Analyze streaks (consecutive runs of the same outcome).

    If outcomes are independent with probability p, streak lengths
    follow a geometric distribution: P(streak = k) = p^(k-1) * (1-p).

    We compare the observed streak distribution to this theoretical one.
    """
    outcomes = df["outcome"].values
    n = len(outcomes)

    # Compute all streaks
    streaks = []
    current_outcome = outcomes[0]
    current_length = 1

    for i in range(1, n):
        if outcomes[i] == current_outcome:
            current_length += 1
        else:
            streaks.append({"outcome": current_outcome, "length": current_length})
            current_outcome = outcomes[i]
            current_length = 1
    streaks.append({"outcome": current_outcome, "length": current_length})

    # Separate by outcome type
    results = {"total_streaks": len(streaks)}

    for outcome in ["HEADS", "TAILS"]:
        outcome_streaks = [s["length"] for s in streaks if s["outcome"] == outcome]
        if not outcome_streaks:
            continue

        lengths = np.array(outcome_streaks)
        count_by_length = Counter(outcome_streaks)

        # Statistics
        mean_len = np.mean(lengths)
        max_len = int(np.max(lengths))
        total_streaks = len(outcome_streaks)

        # Theoretical geometric distribution
        # For a binary sequence with probability p, expected mean streak = 1/(1-p)
        # Here p = P(same outcome next) ≈ P(outcome)
        # Use observed frequency
        p_outcome = np.sum(outcomes == outcome) / n

        # Expected mean streak length for geometric distribution
        p_continuation = p_outcome  # probability of continuing the streak
        expected_mean = 1 / (1 - p_continuation) if p_continuation < 1 else float("inf")

        # Build distribution comparison
        distribution = {}
        for k in range(1, max_len + 1):
            observed_count = count_by_length.get(k, 0)
            # Geometric PMF: P(X = k) = (1-p) * p^(k-1)
            # But for streaks in a sequence, the distribution is slightly different
            # We use the simplified model: P(streak >= k) = p^(k-1)
            expected_pct = (1 - p_continuation) * p_continuation**(k - 1)
            expected_count = expected_pct * total_streaks

            distribution[k] = {
                "observed": observed_count,
                "expected": round(expected_count, 2),
                "observed_pct": round(observed_count / total_streaks * 100, 2),
                "expected_pct": round(expected_pct * 100, 2),
            }

        # Kolmogorov-Smirnov test: do streak lengths follow geometric distribution?
        if len(outcome_streaks) >= 10:
            # Generate geometric samples for comparison
            # scipy's geom PMF: P(X=k) = (1-p)^(k-1) * p, k=1,2,...
            # Here p = 1 - p_continuation (probability of stopping)
            p_stop = 1 - p_continuation
            if 0 < p_stop < 1:
                ks_stat, ks_p = sp_stats.kstest(lengths, "geom", args=(p_stop,))
                ks_result = {
                    "test": "Kolmogorov-Smirnov vs geometric distribution",
                    "ks_statistic": round(float(ks_stat), 6),
                    "p_value": round(float(ks_p), 8),
                    "significant_at_005": ks_p < 0.05,
                    "interpretation": (
                        "Streak distribution significantly deviates from geometric — "
                        "streaks are NOT consistent with independent outcomes."
                        if ks_p < 0.05
                        else "Streak distribution is consistent with geometric — "
                        "no evidence of non-independence."
                    ),
                }
            else:
                ks_result = {"test": "KS test skipped — degenerate probability"}
        else:
            ks_result = {"test": "KS test skipped — fewer than 10 streaks"}

        results[outcome] = {
            "total_streaks": total_streaks,
            "mean_length": round(float(mean_len), 4),
            "max_length": max_len,
            "expected_mean_length": round(expected_mean, 4),
            "p_outcome": round(float(p_outcome), 6),
            "distribution": distribution,
            "ks_test": ks_result,
        }

    # MIDDLE streaks (usually length 1 since MIDDLE is rare)
    middle_streaks = [s["length"] for s in streaks if s["outcome"] == "MIDDLE"]
    if middle_streaks:
        results["MIDDLE"] = {
            "total_streaks": len(middle_streaks),
            "mean_length": round(float(np.mean(middle_streaks)), 4),
            "max_length": int(np.max(middle_streaks)),
            "length_distribution": dict(Counter(middle_streaks)),
        }

    # Longest streaks overall
    top_streaks = sorted(streaks, key=lambda s: s["length"], reverse=True)[:10]
    results["top_10_longest_streaks"] = [
        {"outcome": s["outcome"], "length": s["length"]} for s in top_streaks
    ]

    return results


# ---------------------------------------------------------------------------
# 8. FFT PERIODICITY DETECTION
# ---------------------------------------------------------------------------

def fft_periodicity(df: pd.DataFrame) -> dict:
    """
    Use FFT (Fast Fourier Transform) to detect periodic patterns
    in the outcome sequence.

    If the RNG is truly random, the power spectrum should be flat
    (white noise). Peaks in the spectrum indicate periodic structure.
    """
    outcomes = df["outcome"].values
    encoding = {"HEADS": 1, "TAILS": -1, "MIDDLE": 0}
    seq = np.array([encoding[o] for o in outcomes], dtype=float)

    n = len(seq)
    if n < 32:
        return {"error": "Need at least 32 data points for FFT analysis"}

    # Remove mean (detrend)
    seq_centered = seq - np.mean(seq)

    # Compute FFT
    fft_vals = np.fft.rfft(seq_centered)
    power_spectrum = np.abs(fft_vals) ** 2
    frequencies = np.fft.rfftfreq(n)

    # Normalize power spectrum
    total_power = np.sum(power_spectrum[1:])  # exclude DC component
    if total_power == 0:
        return {"error": "Zero total power — constant sequence"}
    normalized_power = power_spectrum[1:] / total_power
    freq_axis = frequencies[1:]

    # Find peaks above noise threshold
    # For white noise, each frequency bin should have ~1/M of the power
    # where M = number of frequency bins
    m = len(normalized_power)
    noise_threshold = 3.0 / m  # 3x expected power for white noise

    peaks = []
    for i in range(len(normalized_power)):
        if normalized_power[i] > noise_threshold:
            period = 1 / freq_axis[i] if freq_axis[i] > 0 else float("inf")
            peaks.append({
                "frequency": round(float(freq_axis[i]), 6),
                "period": round(float(period), 2),
                "normalized_power": round(float(normalized_power[i]), 6),
                "power_ratio_vs_noise": round(float(normalized_power[i] * m), 4),
            })

    # Sort by power
    peaks.sort(key=lambda p: p["normalized_power"], reverse=True)

    # Fisher's test for periodicity
    # g = max(periodogram) / sum(periodogram)
    # Under null (white noise), g follows a known distribution
    if m > 1:
        g = float(np.max(normalized_power))
        # Approximate p-value: P(g > observed) ≈ m * (1-g)^(m-1)
        fisher_p = min(1.0, m * (1 - g) ** (m - 1))
        fisher_test = {
            "test": "Fisher's exact test for periodicity",
            "g_statistic": round(g, 6),
            "p_value": round(fisher_p, 8),
            "significant_at_005": fisher_p < 0.05,
            "interpretation": (
                "Significant periodicity detected — the sequence has a repeating pattern."
                if fisher_p < 0.05
                else "No significant periodicity — consistent with white noise."
            ),
        }
    else:
        fisher_test = {"test": "Fisher's test skipped — insufficient frequency bins"}

    return {
        "n_samples": n,
        "n_frequency_bins": m,
        "noise_threshold": round(noise_threshold, 6),
        "peaks_above_threshold": peaks[:20],  # top 20
        "n_peaks": len(peaks),
        "fisher_test": fisher_test,
    }


# ---------------------------------------------------------------------------
# 9. CONDITIONAL PROBABILITY ANALYSIS
# ---------------------------------------------------------------------------

def conditional_probability_analysis(df: pd.DataFrame, max_context: int = 5) -> dict:
    """
    Compute conditional probabilities:
      P(HEADS | last k outcomes were all TAILS)
      P(TAILS | last k outcomes were all HEADS)
      etc.

    If the gambler's fallacy were ever correct, we'd see these deviate
    from marginal probabilities. If the game has "correction" mechanisms,
    this is where they'd show up.
    """
    outcomes = df["outcome"].values
    n = len(outcomes)
    marginal = dict(pd.Series(outcomes).value_counts(normalize=True))

    results = {"marginal_probabilities": {k: round(v, 6) for k, v in marginal.items()}}
    conditional_results = {}

    for context_outcome in ["HEADS", "TAILS"]:
        for k in range(1, max_context + 1):
            # Find all positions where the last k outcomes were all context_outcome
            positions = []
            for i in range(k, n):
                if all(outcomes[i - j - 1] == context_outcome for j in range(k)):
                    positions.append(i)

            if len(positions) < 5:
                continue

            # What follows?
            following = [outcomes[pos] for pos in positions]
            follow_counts = Counter(following)
            follow_total = len(following)

            label = f"after_{k}_consecutive_{context_outcome}"
            conditional_results[label] = {
                "context": f"Last {k} outcomes were all {context_outcome}",
                "n_occurrences": follow_total,
                "next_outcome_distribution": {
                    out: {
                        "count": follow_counts.get(out, 0),
                        "proportion": round(follow_counts.get(out, 0) / follow_total, 6),
                        "vs_marginal": round(
                            follow_counts.get(out, 0) / follow_total - marginal.get(out, 0), 6
                        ),
                    }
                    for out in ["HEADS", "TAILS", "MIDDLE"]
                },
            }

            # Binomial test: is P(same outcome | k consecutive) different from marginal?
            same_count = follow_counts.get(context_outcome, 0)
            p_marginal = marginal.get(context_outcome, 0.5)
            if follow_total >= 5 and 0 < p_marginal < 1:
                binom = sp_stats.binomtest(same_count, follow_total, p_marginal)
                conditional_results[label]["binomial_test"] = {
                    "test": f"P({context_outcome}|{k}×{context_outcome}) vs marginal",
                    "observed": round(same_count / follow_total, 6),
                    "expected": round(p_marginal, 6),
                    "p_value": round(float(binom.pvalue), 8),
                    "significant_at_005": binom.pvalue < 0.05,
                }

    results["conditional_after_streaks"] = conditional_results
    return results


# ---------------------------------------------------------------------------
# 10. TIME-OF-DAY ANALYSIS
# ---------------------------------------------------------------------------

def time_of_day_analysis(df: pd.DataFrame) -> dict:
    """
    Check if outcome distribution varies by hour of day.

    If the RNG seed or algorithm changes at certain times, or if
    server load affects the RNG, this would detect it.
    """
    if "timestamp" not in df.columns or df["timestamp"].isna().all():
        return {"error": "No timestamp data available"}

    df_copy = df.copy()
    df_copy["hour"] = df_copy["timestamp"].dt.hour

    results = {"hourly_breakdown": {}}
    hourly_middle_rates = []

    for hour in sorted(df_copy["hour"].unique()):
        hour_data = df_copy[df_copy["hour"] == hour]
        n = len(hour_data)
        counts = hour_data["outcome"].value_counts()

        h = int(counts.get("HEADS", 0))
        t = int(counts.get("TAILS", 0))
        m = int(counts.get("MIDDLE", 0))

        results["hourly_breakdown"][int(hour)] = {
            "n": n,
            "HEADS": h,
            "TAILS": t,
            "MIDDLE": m,
            "HEADS_pct": round(h / n * 100, 2) if n > 0 else 0,
            "TAILS_pct": round(t / n * 100, 2) if n > 0 else 0,
            "MIDDLE_pct": round(m / n * 100, 2) if n > 0 else 0,
        }
        if n >= 10:
            hourly_middle_rates.append(m / n)

    # Chi-squared test: is outcome distribution independent of hour?
    # Build contingency table: hours × outcomes
    hours_with_data = sorted(df_copy["hour"].unique())
    if len(hours_with_data) >= 2:
        contingency = np.zeros((len(hours_with_data), 3), dtype=int)
        for i, hour in enumerate(hours_with_data):
            hour_data = df_copy[df_copy["hour"] == hour]
            counts = hour_data["outcome"].value_counts()
            contingency[i, 0] = counts.get("HEADS", 0)
            contingency[i, 1] = counts.get("TAILS", 0)
            contingency[i, 2] = counts.get("MIDDLE", 0)

        # Remove rows/columns that are all zero
        row_mask = contingency.sum(axis=1) > 0
        col_mask = contingency.sum(axis=0) > 0
        contingency_clean = contingency[row_mask][:, col_mask]

        if contingency_clean.shape[0] >= 2 and contingency_clean.shape[1] >= 2:
            chi2, p_value, dof, expected = sp_stats.chi2_contingency(contingency_clean)
            results["time_independence_test"] = {
                "test": "Chi-squared test of independence (hour × outcome)",
                "chi2_statistic": round(float(chi2), 6),
                "degrees_of_freedom": int(dof),
                "p_value": round(float(p_value), 8),
                "significant_at_005": p_value < 0.05,
                "interpretation": (
                    "Outcome distribution depends on hour of day — possible time-based pattern."
                    if p_value < 0.05
                    else "No significant time-of-day effect on outcomes."
                ),
            }

    return results


# ---------------------------------------------------------------------------
# REPORT GENERATION
# ---------------------------------------------------------------------------

def generate_report(results: dict, df: pd.DataFrame) -> str:
    """Generate a human-readable analysis report."""
    lines = []
    lines.append("=" * 72)
    lines.append("FLIP DA' COIN — STATISTICAL ANALYSIS REPORT")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("=" * 72)

    n = len(df)
    lines.append(f"\nDataset: {n} coin flips")
    if n > 0:
        lines.append(f"Period: {df['timestamp'].min()} to {df['timestamp'].max()}")

    # 1. Descriptive Stats
    desc = results.get("descriptive_stats", {})
    lines.append("\n" + "-" * 72)
    lines.append("1. DESCRIPTIVE STATISTICS")
    lines.append("-" * 72)
    for outcome in ["HEADS", "TAILS", "MIDDLE"]:
        o = desc.get("outcomes", {}).get(outcome, {})
        if o:
            lines.append(
                f"  {outcome:8s}: {o['count']:6d} ({o['pct']:6.2f}%)  "
                f"95% CI: [{o['ci_95_low_pct']:.2f}%, {o['ci_95_high_pct']:.2f}%]"
            )
    lines.append(f"\n  House Edge (= MIDDLE rate): {desc.get('house_edge_pct', '?')}%")
    p_h_given_not_m = desc.get("p_heads_given_not_middle")
    if p_h_given_not_m is not None:
        lines.append(
            f"  P(HEADS | not MIDDLE): {p_h_given_not_m:.4f} "
            f"({'symmetric' if abs(p_h_given_not_m - 0.5) < 0.02 else 'ASYMMETRIC'})"
        )

    # 2. Chi-squared tests
    chi2 = results.get("chi_squared", {})
    lines.append("\n" + "-" * 72)
    lines.append("2. CHI-SQUARED TESTS")
    lines.append("-" * 72)
    sym = chi2.get("heads_tails_symmetry", {})
    if sym:
        lines.append(f"  H/T Symmetry: χ²={sym.get('chi2_statistic', '?')}, "
                      f"p={sym.get('p_value', '?')}")
        lines.append(f"    → {sym.get('interpretation', '')}")

    for key in ["binomial_middle_1pct", "binomial_middle_2pct",
                "binomial_middle_3pct", "binomial_middle_5pct"]:
        bt = chi2.get(key, {})
        if bt:
            lines.append(f"  {bt.get('test', key)}: p={bt.get('p_value', '?')}")

    # 3. Runs test
    runs = results.get("runs_test", {})
    lines.append("\n" + "-" * 72)
    lines.append("3. RUNS TEST (Wald-Wolfowitz)")
    lines.append("-" * 72)
    for key, test in runs.items():
        if isinstance(test, dict) and "p_value" in test:
            sig = "SIGNIFICANT" if test.get("significant_at_005") else "not significant"
            lines.append(f"  {test.get('test', key)}")
            lines.append(f"    z={test.get('z_statistic', '?')}, "
                          f"p={test.get('p_value', '?')} ({sig})")

    # 4. Autocorrelation
    acf = results.get("autocorrelation", {})
    lines.append("\n" + "-" * 72)
    lines.append("4. AUTOCORRELATION ANALYSIS")
    lines.append("-" * 72)
    sig_lags = acf.get("significant_lags", [])
    lines.append(f"  Significant lags: {sig_lags if sig_lags else 'None'}")
    lines.append(f"  95% CI bound: ±{acf.get('ci_95_bound', '?')}")
    lb = acf.get("ljung_box", {})
    if lb:
        lines.append(f"  Ljung-Box: Q={lb.get('Q_statistic', '?')}, p={lb.get('p_value', '?')}")
        lines.append(f"    → {lb.get('interpretation', '')}")

    # 5. Transition matrix
    trans = results.get("transition_matrix", {})
    lines.append("\n" + "-" * 72)
    lines.append("5. TRANSITION MATRIX")
    lines.append("-" * 72)
    probs = trans.get("transition_probabilities", {})
    if probs:
        lines.append("  P(next | prev):")
        lines.append(f"  {'':10s} → HEADS    → TAILS   → MIDDLE")
        for from_label in ["HEADS", "TAILS", "MIDDLE"]:
            row = probs.get(from_label, {})
            lines.append(
                f"  {from_label:10s}   {row.get('HEADS', 0):.4f}    "
                f"{row.get('TAILS', 0):.4f}    {row.get('MIDDLE', 0):.4f}"
            )
    indep = trans.get("independence_test", {})
    if "p_value" in indep:
        lines.append(f"\n  Independence test: χ²={indep['chi2_statistic']}, "
                      f"p={indep['p_value']}")
        lines.append(f"    → {indep.get('interpretation', '')}")

    # 6. Streak analysis
    streaks = results.get("streak_analysis", {})
    lines.append("\n" + "-" * 72)
    lines.append("6. STREAK ANALYSIS")
    lines.append("-" * 72)
    for outcome in ["HEADS", "TAILS"]:
        s = streaks.get(outcome, {})
        if s:
            lines.append(f"  {outcome} streaks:")
            lines.append(f"    Count: {s['total_streaks']}, "
                          f"Mean length: {s['mean_length']:.2f}, "
                          f"Max: {s['max_length']}")
            lines.append(f"    Expected mean (geometric): {s['expected_mean_length']:.2f}")
            ks = s.get("ks_test", {})
            if "p_value" in ks:
                lines.append(f"    KS test: p={ks['p_value']} — {ks.get('interpretation', '')}")

    top = streaks.get("top_10_longest_streaks", [])
    if top:
        lines.append(f"  Top streaks: " + ", ".join(
            f"{s['outcome']}×{s['length']}" for s in top[:5]
        ))

    # 7. FFT
    fft = results.get("fft_periodicity", {})
    lines.append("\n" + "-" * 72)
    lines.append("7. FFT PERIODICITY DETECTION")
    lines.append("-" * 72)
    fisher = fft.get("fisher_test", {})
    if fisher:
        lines.append(f"  Fisher's test: g={fisher.get('g_statistic', '?')}, "
                      f"p={fisher.get('p_value', '?')}")
        lines.append(f"    → {fisher.get('interpretation', '')}")
    peaks = fft.get("peaks_above_threshold", [])
    if peaks:
        lines.append(f"  Peaks above noise ({len(peaks)} total):")
        for p in peaks[:5]:
            lines.append(f"    Period={p['period']:.1f} rounds, "
                          f"power ratio={p['power_ratio_vs_noise']:.2f}x noise")

    # 8. Conditional probability
    cond = results.get("conditional_probability", {})
    lines.append("\n" + "-" * 72)
    lines.append("8. CONDITIONAL PROBABILITY (Gambler's Fallacy Test)")
    lines.append("-" * 72)
    cond_streaks = cond.get("conditional_after_streaks", {})
    for label, data in cond_streaks.items():
        bt = data.get("binomial_test", {})
        if bt and bt.get("significant_at_005"):
            lines.append(f"  *** {bt['test']}: p={bt['p_value']} — SIGNIFICANT ***")
            lines.append(f"      Observed: {bt['observed']:.4f}, Expected: {bt['expected']:.4f}")
    if not any(d.get("binomial_test", {}).get("significant_at_005")
               for d in cond_streaks.values()):
        lines.append("  No significant deviations — no gambler's fallacy correction detected.")

    # 9. Time-of-day
    tod = results.get("time_of_day", {})
    lines.append("\n" + "-" * 72)
    lines.append("9. TIME-OF-DAY ANALYSIS")
    lines.append("-" * 72)
    tod_test = tod.get("time_independence_test", {})
    if "p_value" in tod_test:
        lines.append(f"  Independence test: χ²={tod_test['chi2_statistic']}, "
                      f"p={tod_test['p_value']}")
        lines.append(f"    → {tod_test.get('interpretation', '')}")
    elif "error" in tod:
        lines.append(f"  {tod['error']}")

    # 10. Strategy backtest summary (if available)
    strat = results.get("strategy_backtest", {})
    if strat:
        lines.append("\n" + "-" * 72)
        lines.append("10. STRATEGY BACKTESTING")
        lines.append("-" * 72)
        for name, s in strat.items():
            if isinstance(s, dict):
                roi = s.get("roi_pct", "?")
                final = s.get("final_bankroll", "?")
                lines.append(f"  {name}: ROI={roi}%, Final={final}")

    # Overall verdict
    lines.append("\n" + "=" * 72)
    lines.append("OVERALL VERDICT")
    lines.append("=" * 72)

    issues = []
    if chi2.get("heads_tails_symmetry", {}).get("significant_at_005"):
        issues.append("HEADS/TAILS asymmetry detected")
    if acf.get("any_significant"):
        issues.append(f"Autocorrelation at lags {acf['significant_lags']}")
    if lb.get("significant_at_005"):
        issues.append("Ljung-Box test significant (sequence not white noise)")
    if trans.get("independence_test", {}).get("significant_at_005"):
        issues.append("Transition probabilities not independent")
    if fft.get("fisher_test", {}).get("significant_at_005"):
        issues.append("Periodic structure detected in FFT")
    if any(d.get("binomial_test", {}).get("significant_at_005")
           for d in cond_streaks.values()):
        issues.append("Conditional probability deviates after streaks")
    if tod.get("time_independence_test", {}).get("significant_at_005"):
        issues.append("Time-of-day effect detected")

    if issues:
        lines.append("  ⚠ EXPLOITABLE PATTERNS FOUND:")
        for issue in issues:
            lines.append(f"    • {issue}")
    else:
        lines.append("  ✓ No exploitable patterns detected.")
        lines.append("  The game appears to use a fair RNG.")
        lines.append(f"  House edge ≈ {desc.get('house_edge_pct', '?')}% (MIDDLE rate)")
        lines.append("  No betting strategy can overcome the house edge.")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Flip Da' Coin — Statistical Analysis"
    )
    parser.add_argument(
        "--min-rounds", type=int, default=30,
        help="Minimum rounds required to run analysis (default: 30)"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output raw JSON results instead of formatted report"
    )
    parser.add_argument(
        "--save", action="store_true",
        help="Save report to reports/ directory"
    )
    args = parser.parse_args()

    # Connect to database
    conn = get_connection()
    init_db(conn)

    # Load data
    df = load_flips(conn)
    n = len(df)

    if n == 0:
        print("No data collected yet.")
        print("Run: python -m src bot --rounds 100")
        conn.close()
        sys.exit(1)

    if n < args.min_rounds:
        print(f"Only {n} rounds collected. Need at least {args.min_rounds} for analysis.")
        print(f"Run: python -m src bot --rounds {args.min_rounds}")
        conn.close()
        sys.exit(1)

    print(f"Analyzing {n} coin flips...\n")

    # Run all analyses
    results = {}
    results["descriptive_stats"] = descriptive_stats(df)
    print("  ✓ Descriptive statistics")

    results["chi_squared"] = chi_squared_test(df)
    print("  ✓ Chi-squared tests")

    results["runs_test"] = runs_test(df)
    print("  ✓ Runs test")

    results["autocorrelation"] = autocorrelation_analysis(df)
    print("  ✓ Autocorrelation analysis")

    results["transition_matrix"] = transition_matrix_analysis(df)
    print("  ✓ Transition matrix")

    results["streak_analysis"] = streak_analysis(df)
    print("  ✓ Streak analysis")

    results["fft_periodicity"] = fft_periodicity(df)
    print("  ✓ FFT periodicity detection")

    results["conditional_probability"] = conditional_probability_analysis(df)
    print("  ✓ Conditional probability analysis")

    results["time_of_day"] = time_of_day_analysis(df)
    print("  ✓ Time-of-day analysis")

    # Import and run strategy backtest
    from src.strategies import backtest_all
    results["strategy_backtest"] = backtest_all(df)
    print("  ✓ Strategy backtesting")

    print()

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        report = generate_report(results, df)
        print(report)

        if args.save:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_path = REPORT_DIR / f"analysis_{timestamp}.txt"
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(report)
            print(f"\nReport saved to: {report_path}")

            # Also save raw JSON
            json_path = REPORT_DIR / f"analysis_{timestamp}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, default=str)
            print(f"JSON saved to:   {json_path}")

    conn.close()


if __name__ == "__main__":
    main()
