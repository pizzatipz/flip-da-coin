# Research Plan: Flip Da' Coin RNG Study

## 1. Overview

This document outlines the methodology for studying SportyBet's "Flip Da' Coin" game — a simple coin-flip game where the house edge comes from a rare MIDDLE outcome.

**Game URL**: `https://www.sportybet.com/ng/games/flip-da-coin`

### Game Mechanics

- Player bets on HEADS or TAILS
- Coin flips and lands on one of three outcomes: HEADS, TAILS, MIDDLE
- Correct prediction pays 2X (double the stake)
- MIDDLE = both sides lose (this is the house edge)
- Each round is independent (no carry-over between rounds)
- Multiple players can bet simultaneously

### Key Variables

| Variable | What It Tells Us |
|----------|-----------------|
| P(HEADS) | Probability of heads |
| P(TAILS) | Probability of tails |
| P(MIDDLE) | House edge = P(MIDDLE) |
| Symmetry | P(HEADS) ≈ P(TAILS)? |
| Autocorrelation | Does outcome N predict outcome N+1? |
| Streaks | Are runs of same outcome normal or abnormal? |

## 2. Data Collection

### Phase 1: Observation (No Betting)

The game shows results to all players. We can observe rounds without betting by:
1. Opening the game page
2. Watching the coin flip result each round
3. Recording: round_id, timestamp, outcome (HEADS/TAILS/MIDDLE)

**Target**: 1,000+ rounds for statistical significance.

### Phase 2: Historical Data

The game page shows "Total Bets" for the current round and possibly recent history. We'll scrape any available historical results.

### Data Points Per Round

- Round ID / number
- Timestamp
- Outcome: HEADS, TAILS, or MIDDLE
- Any visible statistics (player count, total bets, etc.)

## 3. Analysis Pipeline

### 3.1 Descriptive Statistics
- HEADS/TAILS/MIDDLE frequency
- Confidence intervals for each probability
- Estimated house edge

### 3.2 Independence Tests
- Chi-squared goodness-of-fit (vs expected 49/49/2 split)
- Runs test (is the sequence of H/T random?)
- Autocorrelation at lags 1-20
- Transition matrix (does H→H differ from T→H?)

### 3.3 Pattern Detection
- Streak distribution (compare to geometric distribution)
- Time-series analysis (FFT for periodicity)
- Conditional probability (P(HEADS | last 3 were TAILS))

### 3.4 Strategy Backtesting
- Flat betting (always HEADS)
- Martingale (double after loss)
- Anti-streak (bet opposite of last result)
- Pattern-following (bet same as last result)
- Statistical trigger (bet after N consecutive same outcomes)

## 4. Expected Findings

If the RNG is fair:
- P(HEADS) = P(TAILS) = (1 - P(MIDDLE)) / 2
- All strategies converge to -P(MIDDLE) × 100% ROI
- No autocorrelation at any lag
- Streak distribution matches geometric distribution

If the RNG has exploitable structure:
- P(HEADS) ≠ P(TAILS) → bet the more frequent side
- Autocorrelation found → sequential prediction possible
- Non-geometric streak distribution → streak-based strategy viable

## 5. Technical Stack

- **Python 3.12+** with virtual environment
- **Playwright** for browser automation
- **SQLite** for data storage
- **NumPy/SciPy** for statistical tests
- **Pandas** for data manipulation

## 6. Success Criteria

The study succeeds if we can definitively answer:
1. What is the exact P(MIDDLE) and thus the house edge?
2. Is the game fair (HEADS = TAILS) excluding MIDDLE?
3. Are outcomes independent? (no exploitable patterns)
4. Can any strategy overcome the house edge?
