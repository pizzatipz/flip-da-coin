# Flip Da' Coin — RNG Study

An empirical study of SportyBet's "Flip Da' Coin" game to determine whether the coin flip outcomes exhibit any detectable patterns or exploitable structure.

## The Game

**Flip Da' Coin** is a simple coin-flip betting game on SportyBet Nigeria.

| Aspect | Detail |
|--------|--------|
| URL | `https://www.sportybet.com/ng/games/flip-da-coin` |
| Outcomes | HEADS, TAILS, or MIDDLE |
| Payout | 2X on correct HEADS/TAILS prediction |
| House Edge | MIDDLE outcome — both HEADS and TAILS bets lose |
| Type | Quick game (instant result per round) |

### How It Works

1. Player selects **HEADS** or **TAILS** and places a bet
2. A coin flips with animation
3. The coin lands on HEADS, TAILS, or (rarely) MIDDLE
4. If the player predicted correctly → **2X payout** (double the stake)
5. If wrong or MIDDLE → lose the stake

### The House Edge

The game pays 2X on a correct prediction, which would be fair (0% edge) if outcomes were exactly 50/50. The house edge comes from the **MIDDLE** outcome — when the coin lands on its edge, ALL bets lose regardless of selection.

**Key research question:** How often does MIDDLE occur? If MIDDLE = 2%, then:
- P(HEADS) ≈ P(TAILS) ≈ 49%
- EV per bet = 0.49 × 2 - 1 = -0.02 = **-2% house edge**

The exact MIDDLE frequency determines the true house edge, and any asymmetry between HEADS and TAILS frequencies would be an exploitable bias.

## Research Goals

1. **Measure the exact MIDDLE frequency** — This directly determines the house edge
2. **Test HEADS vs TAILS symmetry** — Are they truly 50/50 (excluding MIDDLE)?
3. **Check for sequential patterns** — Autocorrelation, streaks, runs tests
4. **Check for time-of-day patterns** — Does the RNG behave differently at different times?
5. **Determine if any betting strategy can overcome the house edge**

## Project Structure

```
flip-da-coin/
├── README.md              # This file
├── PLAN.md                # Research plan and methodology
├── requirements.txt       # Python dependencies
├── src/
│   ├── __init__.py
│   ├── bot.py             # Playwright automation for data collection
│   ├── db.py              # SQLite storage layer
│   ├── analyze.py         # Statistical analysis pipeline
│   └── strategies.py      # Strategy backtesting
├── data/
│   └── flipdacoin.db      # SQLite database (gitignored)
└── reports/               # Generated analysis reports
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt
playwright install chromium

# Collect data (observe mode)
python -m src bot --rounds 100

# Run analysis
python -m src analyze
```

## Hypothesis

The game uses a certified CSPRNG. If correctly implemented:
- HEADS and TAILS should each occur at approximately (1 - P(MIDDLE)) / 2
- MIDDLE frequency determines the exact house edge
- No sequential pattern should exist between consecutive flips
- No betting strategy should produce positive expected value

**We verify, not assume.**

## License

MIT — This is a research project.