"""
Playwright bot for observing and recording Flip Da' Coin outcomes.

Architecture:
  - Opens the game page in a Playwright-controlled browser
  - User logs in manually (first run) or session is restored
  - Intercepts WebSocket messages via Playwright's WS handler
  - Parses STOMP frames for ROUND_GENERATED messages
  - Extracts roundId, currentDraw, timeStamp
  - Records to SQLite

Protocol discovered via DOM/network inspection:
  - Game uses STOMP over WebSocket
  - Topic: /topic/ng-round-update
  - Message type: ROUND_GENERATED (with hasEnded=true) = final result
  - currentDraw values: "Heads", "Tails", "Middle"
  - Round lifecycle: ~13s (7s betting + 1s init + 2s draw + 3s result)
"""

import argparse
import json
import re
import time
import signal
import sys
from pathlib import Path
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from src.db import get_connection, init_db, insert_flip, get_stats

LOBBY_URL = "https://www.sportybet.com/ng/games?source=TopRibbon"
PROFILE_DIR = Path(__file__).parent.parent / "data" / "browser_profile"

# Map game's draw values to our canonical outcome names
DRAW_MAP = {
    "Heads": "HEADS",
    "Tails": "TAILS",
    "Middle": "MIDDLE",
    "heads": "HEADS",
    "tails": "TAILS",
    "middle": "MIDDLE",
    "HEADS": "HEADS",
    "TAILS": "TAILS",
    "MIDDLE": "MIDDLE",
}


def parse_stomp_frame(raw: str) -> dict | None:
    """
    Parse a STOMP frame from a WebSocket text message.

    STOMP frames look like:
        MESSAGE
        destination:/topic/ng-round-update
        content-type:text/plain;charset=UTF-8
        subscription:sub-0
        message-id:...
        content-length:159

        {"roundId":6841247,"currentDraw":"Heads",...}

    Returns dict with 'command', 'headers', 'body' or None if not STOMP.
    """
    if not raw or not isinstance(raw, str):
        return None

    # STOMP frames have a command line, headers, blank line, then body
    # The command is the first line
    lines = raw.split("\n")
    if not lines:
        return None

    command = lines[0].strip()
    if command not in ("MESSAGE", "CONNECTED", "ERROR", "RECEIPT"):
        return None

    headers = {}
    body_start = None
    for i, line in enumerate(lines[1:], 1):
        stripped = line.strip()
        if not stripped:
            body_start = i + 1
            break
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            headers[key] = value

    body = None
    if body_start is not None and body_start < len(lines):
        body_raw = "\n".join(lines[body_start:]).strip()
        # Remove STOMP null terminator
        body_raw = body_raw.rstrip("\x00")
        if body_raw:
            try:
                body = json.loads(body_raw)
            except json.JSONDecodeError:
                body = body_raw

    return {
        "command": command,
        "headers": headers,
        "body": body,
    }


class FlipCollector:
    """Collects coin flip results from WebSocket messages."""

    def __init__(self, conn, target_rounds: int = 0, quiet: bool = False):
        self.conn = conn
        self.target_rounds = target_rounds
        self.quiet = quiet
        self.rounds_collected = 0
        self.seen_round_ids = set()
        self.running = True
        self.last_round_time = None

    def handle_ws_message(self, payload: str):
        """Process a WebSocket message, looking for round results."""
        if not isinstance(payload, str):
            return

        # Quick check: does this message contain game data at all?
        if "ROUND_GENERATED" in payload:
            # Extract JSON directly from the raw payload
            json_start = payload.find("{")
            if json_start >= 0:
                json_str = payload[json_start:].rstrip("\x00").strip()
                try:
                    body = json.loads(json_str)
                    if isinstance(body, dict):
                        self._process_game_message(body)
                        return
                except json.JSONDecodeError:
                    pass

        # Try to parse as STOMP frame
        frame = parse_stomp_frame(payload)
        if frame and frame["command"] == "MESSAGE" and frame["body"]:
            body = frame["body"]
            if isinstance(body, dict):
                self._process_game_message(body)
            return

        # Also try direct JSON parsing (in case format differs)
        try:
            data = json.loads(payload)
            if isinstance(data, dict) and "messageType" in data:
                self._process_game_message(data)
        except (json.JSONDecodeError, TypeError):
            pass

    def _process_game_message(self, msg: dict):
        """Process a parsed game message."""
        msg_type = msg.get("messageType", "")

        # We want ROUND_GENERATED with hasEnded=true — this is the final result
        if msg_type.startswith("ROUND_GENERATED") and msg.get("hasEnded") is True:
            round_id = str(msg.get("roundId", ""))
            draw = msg.get("currentDraw", "")
            timestamp_ms = msg.get("timeStamp")

            # Deduplicate — ROUND_GENERATED is sent multiple times per round
            # (once every 500ms during the 3s display window)
            if round_id in self.seen_round_ids:
                return

            # Map draw to canonical outcome
            outcome = DRAW_MAP.get(draw)
            if outcome is None:
                if not self.quiet:
                    print(f"  [WARN] Unknown draw value: {draw!r} in round {round_id}")
                return

            self.seen_round_ids.add(round_id)

            # Create timestamp
            if timestamp_ms:
                ts = datetime.fromtimestamp(
                    timestamp_ms / 1000, tz=timezone.utc
                ).isoformat()
            else:
                ts = datetime.now(timezone.utc).isoformat()

            # Record to database
            row_id = insert_flip(self.conn, outcome, round_id=round_id, timestamp=ts)
            self.rounds_collected += 1
            self.last_round_time = time.time()

            if not self.quiet:
                stats = get_stats(self.conn)
                print(
                    f"  Round {round_id}: {outcome:6s}  "
                    f"[Total: {stats['total']} | "
                    f"H:{stats['heads_pct']:.1f}% "
                    f"T:{stats['tails_pct']:.1f}% "
                    f"M:{stats['middle_pct']:.1f}%]"
                )

            # Check if we've reached target
            if self.target_rounds > 0 and self.rounds_collected >= self.target_rounds:
                self.running = False


def main():
    parser = argparse.ArgumentParser(
        description="Flip Da' Coin — Data Collection Bot"
    )
    parser.add_argument(
        "--rounds", type=int, default=0,
        help="Number of rounds to observe (0 = run until stopped)"
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run browser in headless mode (requires prior login)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-round output"
    )
    args = parser.parse_args()

    # Set up database
    conn = get_connection()
    init_db(conn)

    # Show current stats
    stats = get_stats(conn)
    print(f"Database: {stats['total']} flips recorded so far")
    if stats["total"] > 0:
        print(f"  HEADS: {stats['heads']} ({stats['heads_pct']:.2f}%)")
        print(f"  TAILS: {stats['tails']} ({stats['tails_pct']:.2f}%)")
        print(f"  MIDDLE: {stats['middle']} ({stats['middle_pct']:.2f}%)")

    target_msg = f"{args.rounds} rounds" if args.rounds > 0 else "indefinitely (Ctrl+C to stop)"
    print(f"\nCollecting: {target_msg}")
    print()

    collector = FlipCollector(conn, args.rounds, args.quiet)

    # Use a persistent browser profile directory for true login persistence.
    # This saves cookies, localStorage, sessionStorage, IndexedDB — everything.
    # On subsequent runs, you'll already be logged in.
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=args.headless,
            viewport={"width": 1400, "height": 900},
            args=["--start-maximized"] if not args.headless else [],
        )

        page = context.pages[0] if context.pages else context.new_page()

        # Intercept WebSocket messages via page.on("websocket").
        # Confirmed working: framereceived delivers data as str type.
        def on_websocket(ws):
            ws_url = ws.url
            if not args.quiet:
                print(f"  [WS] Connected: {ws_url[:100]}")

            def on_frame_received(data):
                # data is a str containing the raw STOMP frame text
                collector.handle_ws_message(str(data))

            ws.on("framereceived", on_frame_received)

        page.on("websocket", on_websocket)

        # Navigate to the games lobby — user selects Flip Da' Coin from there
        print(f"Opening games lobby...")
        page.goto(LOBBY_URL, wait_until="networkidle", timeout=60000)

        print()
        print("=" * 55)
        print("  INSTRUCTIONS")
        print("=" * 55)
        print("  1. Log in if needed (session persists between runs)")
        print("  2. Select 'Flip Da' Coin' from the games lobby")
        print("  3. The bot auto-captures every round result")
        print("=" * 55)
        print()
        print("Waiting for game data...\n")

        # Main collection loop — just wait for STOMP messages
        try:
            no_data_warned = False
            start_time = time.time()

            while collector.running:
                try:
                    # CRITICAL: Use page.wait_for_timeout() instead of time.sleep()
                    # time.sleep() blocks Playwright's event loop and prevents
                    # WebSocket frame dispatch. wait_for_timeout() yields properly.
                    page.wait_for_timeout(1000)
                except Exception:
                    # Page or context crashed — try to recover
                    print("  [WARN] Page connection lost. Attempting to recover...")
                    try:
                        time.sleep(5)
                        # Try to navigate back to the game
                        page = context.pages[0] if context.pages else context.new_page()

                        # Re-attach WS listener
                        def on_websocket_recover(ws):
                            ws_url = ws.url
                            if not args.quiet:
                                print(f"  [WS] Reconnected: {ws_url[:100]}")
                            def on_frame_received_recover(data):
                                collector.handle_ws_message(str(data))
                            ws.on("framereceived", on_frame_received_recover)
                        page.on("websocket", on_websocket_recover)

                        page.goto(LOBBY_URL, wait_until="networkidle", timeout=60000)
                        print("  [INFO] Recovered. Select Flip Da' Coin in the browser.")
                        continue
                    except Exception as e2:
                        print(f"  [ERROR] Recovery failed: {e2}")
                        break

                elapsed = time.time() - start_time
                has_data = collector.rounds_collected > 0

                # First-data confirmation
                if has_data and not no_data_warned:
                    pass  # Normal — data is flowing

                # Periodic no-data warning (only after 90s with zero rounds)
                if not has_data and elapsed > 90 and not no_data_warned:
                    no_data_warned = True
                    print("  [INFO] No rounds detected yet.")
                    print("         Make sure you've selected Flip Da' Coin in the browser.")

                # Periodic no-new-data warning
                if (collector.last_round_time and
                        time.time() - collector.last_round_time > 90):
                    if not args.quiet:
                        print("  [INFO] No new rounds in 90s — game may be paused or tab inactive")
                        collector.last_round_time = time.time()  # Reset to avoid spam

        except KeyboardInterrupt:
            print("\n\nStopping collection...")

        context.close()

    # Print final stats
    stats = get_stats(conn)
    print(f"\n{'=' * 50}")
    print(f"COLLECTION COMPLETE")
    print(f"{'=' * 50}")
    print(f"  Rounds collected this session: {collector.rounds_collected}")
    print(f"  Total in database: {stats['total']}")
    if stats["total"] > 0:
        print(f"  HEADS:  {stats['heads']:5d} ({stats['heads_pct']:.2f}%)")
        print(f"  TAILS:  {stats['tails']:5d} ({stats['tails_pct']:.2f}%)")
        print(f"  MIDDLE: {stats['middle']:5d} ({stats['middle_pct']:.2f}%)")
        print(f"  Estimated house edge: {stats['house_edge']:.2f}%")

    conn.close()


if __name__ == "__main__":
    main()
