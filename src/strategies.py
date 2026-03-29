"""
Strategy backtester for Flip Da' Coin.

Tests various betting strategies against collected data to determine
if any approach can overcome the MIDDLE house edge.

Strategies tested:
  1. Flat HEADS      — always bet HEADS, fixed stake
  2. Flat TAILS      — always bet TAILS, fixed stake
  3. Flat MAJORITY   — always bet whichever has higher observed P
  4. Martingale      — double stake after each loss, reset after win
  5. Anti-Martingale — double stake after each win, reset after loss
  6. Anti-Streak     — bet opposite of last result
  7. Follow-Streak   — bet same as last result
  8. Trigger-3       — bet opposite after 3 consecutive same outcomes
  9. Trigger-5       — bet opposite after 5 consecutive same outcomes
  10. D'Alembert     — increase by 1 after loss, decrease by 1 after win

All strategies use a starting bankroll and track:
  - Final bankroll
  - ROI (%)
  - Max drawdown
  - Win rate
  - Number of bets placed
  - Bankroll trajectory
"""

import numpy as np
import pandas as pd
from collections import Counter


DEFAULT_BANKROLL = 1000.0
DEFAULT_BASE_STAKE = 10.0
PAYOUT_MULTIPLIER = 2.0  # correct prediction pays 2x stake


def _resolve_bet(selection: str, outcome: str, stake: float) -> float:
    """
    Resolve a single bet.

    Returns profit (positive = win, negative = loss).
    - Win: +stake (since payout = 2x stake, profit = 2*stake - stake = stake)
    - Loss (wrong or MIDDLE): -stake
    """
    if outcome == "MIDDLE":
        return -stake
    if selection == outcome:
        return stake  # 2x payout minus 1x stake = +1x stake
    return -stake


def _compute_metrics(bets: list, bankroll_history: list,
                     initial_bankroll: float) -> dict:
    """Compute performance metrics from a list of bet results."""
    if not bets:
        return {
            "n_bets": 0,
            "final_bankroll": initial_bankroll,
            "roi_pct": 0.0,
            "note": "No bets placed",
        }

    profits = [b["profit"] for b in bets]
    wins = sum(1 for p in profits if p > 0)
    losses = sum(1 for p in profits if p < 0)
    total_staked = sum(b["stake"] for b in bets)
    total_profit = sum(profits)
    final_bankroll = bankroll_history[-1]

    # Drawdown analysis
    bankroll_arr = np.array(bankroll_history)
    peak = np.maximum.accumulate(bankroll_arr)
    drawdown = (peak - bankroll_arr) / peak
    max_drawdown = float(np.max(drawdown)) if len(drawdown) > 0 else 0

    # Find longest losing streak
    max_losing_streak = 0
    current_losing = 0
    for p in profits:
        if p < 0:
            current_losing += 1
            max_losing_streak = max(max_losing_streak, current_losing)
        else:
            current_losing = 0

    return {
        "n_bets": len(bets),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(bets) * 100, 2),
        "total_staked": round(total_staked, 2),
        "total_profit": round(total_profit, 2),
        "roi_pct": round(total_profit / total_staked * 100, 4) if total_staked > 0 else 0,
        "final_bankroll": round(final_bankroll, 2),
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "max_losing_streak": max_losing_streak,
        "avg_stake": round(total_staked / len(bets), 2),
    }


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def flat_bet(outcomes: np.ndarray, selection: str,
             bankroll: float = DEFAULT_BANKROLL,
             stake: float = DEFAULT_BASE_STAKE) -> dict:
    """Always bet the same side with a fixed stake."""
    bets = []
    bankroll_history = [bankroll]

    for outcome in outcomes:
        if bankroll < stake:
            break  # Busted

        profit = _resolve_bet(selection, outcome, stake)
        bankroll += profit
        bets.append({"selection": selection, "outcome": outcome,
                      "stake": stake, "profit": profit})
        bankroll_history.append(bankroll)

    return _compute_metrics(bets, bankroll_history, DEFAULT_BANKROLL)


def martingale(outcomes: np.ndarray, selection: str,
               bankroll: float = DEFAULT_BANKROLL,
               base_stake: float = DEFAULT_BASE_STAKE,
               max_stake: float = None) -> dict:
    """
    Martingale: double stake after each loss, reset to base after win.

    max_stake limits the doubling to prevent absurd bets.
    """
    if max_stake is None:
        max_stake = bankroll  # Can't bet more than bankroll

    bets = []
    bankroll_history = [bankroll]
    current_stake = base_stake

    for outcome in outcomes:
        actual_stake = min(current_stake, bankroll, max_stake)
        if actual_stake < base_stake:
            break  # Can't afford minimum bet

        profit = _resolve_bet(selection, outcome, actual_stake)
        bankroll += profit
        bets.append({"selection": selection, "outcome": outcome,
                      "stake": actual_stake, "profit": profit})
        bankroll_history.append(bankroll)

        if profit > 0:
            current_stake = base_stake  # Reset after win
        else:
            current_stake *= 2  # Double after loss

    return _compute_metrics(bets, bankroll_history, DEFAULT_BANKROLL)


def anti_martingale(outcomes: np.ndarray, selection: str,
                    bankroll: float = DEFAULT_BANKROLL,
                    base_stake: float = DEFAULT_BASE_STAKE,
                    max_doubles: int = 4) -> dict:
    """
    Anti-Martingale (Paroli): double stake after each win,
    reset to base after loss or after max_doubles consecutive wins.
    """
    bets = []
    bankroll_history = [bankroll]
    current_stake = base_stake
    consecutive_wins = 0

    for outcome in outcomes:
        actual_stake = min(current_stake, bankroll)
        if actual_stake < base_stake:
            break

        profit = _resolve_bet(selection, outcome, actual_stake)
        bankroll += profit
        bets.append({"selection": selection, "outcome": outcome,
                      "stake": actual_stake, "profit": profit})
        bankroll_history.append(bankroll)

        if profit > 0:
            consecutive_wins += 1
            if consecutive_wins >= max_doubles:
                current_stake = base_stake
                consecutive_wins = 0
            else:
                current_stake *= 2
        else:
            current_stake = base_stake
            consecutive_wins = 0

    return _compute_metrics(bets, bankroll_history, DEFAULT_BANKROLL)


def anti_streak(outcomes: np.ndarray,
                bankroll: float = DEFAULT_BANKROLL,
                stake: float = DEFAULT_BASE_STAKE) -> dict:
    """
    Anti-streak: bet the OPPOSITE of the last result.
    Based on the gambler's fallacy that streaks must end.
    First bet is HEADS (arbitrary).
    """
    bets = []
    bankroll_history = [bankroll]
    selection = "HEADS"  # First bet

    for i, outcome in enumerate(outcomes):
        if bankroll < stake:
            break

        profit = _resolve_bet(selection, outcome, stake)
        bankroll += profit
        bets.append({"selection": selection, "outcome": outcome,
                      "stake": stake, "profit": profit})
        bankroll_history.append(bankroll)

        # Next bet: opposite of this outcome
        if outcome == "HEADS":
            selection = "TAILS"
        elif outcome == "TAILS":
            selection = "HEADS"
        # If MIDDLE, keep previous selection

    return _compute_metrics(bets, bankroll_history, DEFAULT_BANKROLL)


def follow_streak(outcomes: np.ndarray,
                  bankroll: float = DEFAULT_BANKROLL,
                  stake: float = DEFAULT_BASE_STAKE) -> dict:
    """
    Follow-streak: bet the SAME as the last result.
    Theory: momentum / hot hand. First bet is HEADS (arbitrary).
    """
    bets = []
    bankroll_history = [bankroll]
    selection = "HEADS"

    for i, outcome in enumerate(outcomes):
        if bankroll < stake:
            break

        profit = _resolve_bet(selection, outcome, stake)
        bankroll += profit
        bets.append({"selection": selection, "outcome": outcome,
                      "stake": stake, "profit": profit})
        bankroll_history.append(bankroll)

        # Next bet: same as this outcome
        if outcome in ("HEADS", "TAILS"):
            selection = outcome
        # If MIDDLE, keep previous selection

    return _compute_metrics(bets, bankroll_history, DEFAULT_BANKROLL)


def trigger_strategy(outcomes: np.ndarray, trigger_count: int,
                     bankroll: float = DEFAULT_BANKROLL,
                     stake: float = DEFAULT_BASE_STAKE) -> dict:
    """
    Trigger: only bet after N consecutive same outcomes, then bet the opposite.

    This tests whether "mean reversion" after long streaks is profitable.
    """
    bets = []
    bankroll_history = [bankroll]

    # Track recent outcomes to detect triggers
    for i in range(trigger_count, len(outcomes)):
        if bankroll < stake:
            break

        # Check if the last trigger_count outcomes were all the same
        recent = outcomes[i - trigger_count:i]
        if len(set(recent)) != 1:
            continue  # Not a trigger

        trigger_outcome = recent[0]
        if trigger_outcome == "MIDDLE":
            continue  # Don't trigger on MIDDLE streaks

        # Bet opposite
        selection = "TAILS" if trigger_outcome == "HEADS" else "HEADS"
        outcome = outcomes[i]

        profit = _resolve_bet(selection, outcome, stake)
        bankroll += profit
        bets.append({"selection": selection, "outcome": outcome,
                      "stake": stake, "profit": profit,
                      "trigger": f"{trigger_count}×{trigger_outcome}"})
        bankroll_history.append(bankroll)

    return _compute_metrics(bets, bankroll_history, DEFAULT_BANKROLL)


def dalembert(outcomes: np.ndarray, selection: str,
              bankroll: float = DEFAULT_BANKROLL,
              base_stake: float = DEFAULT_BASE_STAKE) -> dict:
    """
    D'Alembert: increase stake by base_stake after loss,
    decrease by base_stake after win (minimum = base_stake).

    More conservative than Martingale (linear vs exponential scaling).
    """
    bets = []
    bankroll_history = [bankroll]
    current_stake = base_stake

    for outcome in outcomes:
        actual_stake = min(current_stake, bankroll)
        if actual_stake < base_stake:
            break

        profit = _resolve_bet(selection, outcome, actual_stake)
        bankroll += profit
        bets.append({"selection": selection, "outcome": outcome,
                      "stake": actual_stake, "profit": profit})
        bankroll_history.append(bankroll)

        if profit > 0:
            current_stake = max(base_stake, current_stake - base_stake)
        else:
            current_stake += base_stake

    return _compute_metrics(bets, bankroll_history, DEFAULT_BANKROLL)


# ---------------------------------------------------------------------------
# Run all strategies
# ---------------------------------------------------------------------------

def backtest_all(df: pd.DataFrame) -> dict:
    """Run all strategies against the collected data."""
    outcomes = df["outcome"].values

    if len(outcomes) < 10:
        return {"error": "Need at least 10 rounds for backtesting"}

    # Determine majority outcome for the "majority" strategy
    counts = Counter(outcomes)
    h_count = counts.get("HEADS", 0)
    t_count = counts.get("TAILS", 0)
    majority = "HEADS" if h_count >= t_count else "TAILS"

    results = {}

    # Flat betting strategies
    results["flat_heads"] = flat_bet(outcomes, "HEADS")
    results["flat_heads"]["description"] = "Always bet HEADS, fixed stake"

    results["flat_tails"] = flat_bet(outcomes, "TAILS")
    results["flat_tails"]["description"] = "Always bet TAILS, fixed stake"

    results["flat_majority"] = flat_bet(outcomes, majority)
    results["flat_majority"]["description"] = f"Always bet {majority} (observed majority), fixed stake"

    # Martingale
    results["martingale_heads"] = martingale(outcomes, "HEADS")
    results["martingale_heads"]["description"] = "Martingale on HEADS (double after loss)"

    results["martingale_tails"] = martingale(outcomes, "TAILS")
    results["martingale_tails"]["description"] = "Martingale on TAILS (double after loss)"

    # Anti-Martingale
    results["anti_martingale_heads"] = anti_martingale(outcomes, "HEADS")
    results["anti_martingale_heads"]["description"] = "Anti-Martingale on HEADS (double after win)"

    # Streak-based
    results["anti_streak"] = anti_streak(outcomes)
    results["anti_streak"]["description"] = "Bet opposite of last result"

    results["follow_streak"] = follow_streak(outcomes)
    results["follow_streak"]["description"] = "Bet same as last result"

    # Trigger strategies
    results["trigger_3"] = trigger_strategy(outcomes, 3)
    results["trigger_3"]["description"] = "Bet opposite after 3 consecutive same"

    results["trigger_5"] = trigger_strategy(outcomes, 5)
    results["trigger_5"]["description"] = "Bet opposite after 5 consecutive same"

    # D'Alembert
    results["dalembert_heads"] = dalembert(outcomes, "HEADS")
    results["dalembert_heads"]["description"] = "D'Alembert on HEADS (linear stake increase)"

    return results


def main():
    """Standalone entry point for strategy backtesting."""
    import sys
    from src.db import get_connection, init_db

    conn = get_connection()
    init_db(conn)

    df = pd.read_sql_query(
        "SELECT id, round_id, timestamp, outcome FROM flips ORDER BY id",
        conn,
    )
    conn.close()

    if df.empty:
        print("No data collected yet.")
        print("Run: python -m src bot --rounds 100")
        sys.exit(1)

    print(f"Backtesting {len(df)} rounds...\n")
    results = backtest_all(df)

    # Print results sorted by ROI
    strategies = [(name, data) for name, data in results.items()
                  if isinstance(data, dict) and "roi_pct" in data]
    strategies.sort(key=lambda x: x[1]["roi_pct"], reverse=True)

    print(f"{'Strategy':<30s} {'Bets':>6s} {'Win%':>7s} {'ROI%':>8s} "
          f"{'Final':>10s} {'MaxDD%':>7s} {'MaxLoss':>8s}")
    print("-" * 82)

    for name, data in strategies:
        print(f"{name:<30s} {data['n_bets']:6d} {data['win_rate']:6.1f}% "
              f"{data['roi_pct']:7.2f}% {data['final_bankroll']:10.2f} "
              f"{data['max_drawdown_pct']:6.1f}% {data['max_losing_streak']:8d}")

    print(f"\n{'Description':}")
    for name, data in strategies:
        print(f"  {name}: {data.get('description', '')}")


if __name__ == "__main__":
    main()
