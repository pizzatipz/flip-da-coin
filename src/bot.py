"""
Playwright bot for observing and recording Flip Da' Coin outcomes.

Primary mode: Watch coin flips and record results to build a dataset
for statistical analysis of the RNG.
"""

import argparse
import asyncio

# Placeholder — will be implemented after DOM inspection
GAME_URL = "https://www.sportybet.com/ng/games/flip-da-coin"


def main():
    parser = argparse.ArgumentParser(
        description="Flip Da' Coin — Data Collection Bot"
    )
    parser.add_argument(
        "--rounds", type=int, default=0,
        help="Number of rounds to observe (0 = run until stopped)"
    )
    parser.add_argument(
        "--inspect", action="store_true",
        help="Launch browser in inspect mode to examine page DOM"
    )
    args = parser.parse_args()

    print("Flip Da' Coin bot — not yet implemented")
    print("Run with --inspect first to discover DOM selectors")


if __name__ == "__main__":
    main()
