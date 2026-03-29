"""Test STOMP parser and collector logic."""
import sys
sys.path.insert(0, ".")

from src.bot import parse_stomp_frame, FlipCollector, DRAW_MAP
from src.db import get_connection, init_db
from pathlib import Path

# --- Test 1: STOMP parser with ROUND_GENERATED frame ---
test_frame = (
    "MESSAGE\n"
    "destination:/topic/ng-round-update\n"
    "content-type:text/plain;charset=UTF-8\n"
    "subscription:sub-0\n"
    "message-id:test-123\n"
    "content-length:159\n"
    "\n"
    '{"roundId":6841247,"currentDraw":"Heads","hasEnded":true,'
    '"millisLeft":2500,"totalMillis":3000,'
    '"messageType":"ROUND_GENERATED","timeStamp":1774517938000}'
)

result = parse_stomp_frame(test_frame)
assert result is not None, "Failed to parse STOMP frame"
assert result["command"] == "MESSAGE"
assert result["body"]["roundId"] == 6841247
assert result["body"]["currentDraw"] == "Heads"
assert result["body"]["hasEnded"] is True
assert result["body"]["messageType"] == "ROUND_GENERATED"
print("Test 1 PASSED: STOMP parser (ROUND_GENERATED)")

# --- Test 2: ROUND_WAITING should NOT trigger collection ---
test_waiting = (
    "MESSAGE\n"
    "destination:/topic/ng-round-update\n"
    "content-type:text/plain;charset=UTF-8\n"
    "subscription:sub-0\n"
    "message-id:test-456\n"
    "content-length:112\n"
    "\n"
    '{"roundId":6841248,"millisLeft":7000,"totalMillis":7000,'
    '"messageType":"ROUND_WAITING","timeStamp":1774517941440}'
)

result2 = parse_stomp_frame(test_waiting)
assert result2["body"]["messageType"] == "ROUND_WAITING"
assert "hasEnded" not in result2["body"]
print("Test 2 PASSED: ROUND_WAITING parsed but no hasEnded")

# --- Test 3: Non-STOMP messages should return None ---
result3 = parse_stomp_frame('42["data",{"type":"resp"}]')
assert result3 is None, f"Expected None for non-STOMP, got {result3}"
print("Test 3 PASSED: Non-STOMP returns None")

# --- Test 4: DRAW_MAP covers all cases ---
assert DRAW_MAP["Heads"] == "HEADS"
assert DRAW_MAP["Tails"] == "TAILS"
assert DRAW_MAP["Middle"] == "MIDDLE"
assert DRAW_MAP["heads"] == "HEADS"
assert DRAW_MAP["HEADS"] == "HEADS"
print("Test 4 PASSED: DRAW_MAP covers all variants")

# --- Test 5: FlipCollector integration with real messages ---
TEST_DB = Path("data/test_bot.db")
TEST_DB.parent.mkdir(parents=True, exist_ok=True)
if TEST_DB.exists():
    TEST_DB.unlink()

conn = get_connection(TEST_DB)
init_db(conn)

collector = FlipCollector(conn, target_rounds=3, quiet=True)

# Simulate 3 rounds with ROUND_GENERATED messages
for i, draw in enumerate(["Heads", "Tails", "Middle"]):
    round_id = 6841247 + i
    msg = (
        "MESSAGE\n"
        "destination:/topic/ng-round-update\n"
        "content-type:text/plain;charset=UTF-8\n"
        "subscription:sub-0\n"
        f"message-id:test-{round_id}\n"
        "content-length:180\n"
        "\n"
        f'{{"roundId":{round_id},"currentDraw":"{draw}","hasEnded":true,'
        f'"millisLeft":3000,"totalMillis":3000,'
        f'"messageType":"ROUND_GENERATED","timeStamp":{1774517938000 + i * 13000}}}'
    )
    collector.handle_ws_message(msg)

assert collector.rounds_collected == 3, f"Expected 3 rounds, got {collector.rounds_collected}"
assert not collector.running, "Collector should have stopped after target reached"

# Verify deduplication — send same round again
collector.running = True  # reset for test
collector.handle_ws_message(
    "MESSAGE\n"
    "destination:/topic/ng-round-update\n"
    "content-type:text/plain;charset=UTF-8\n"
    "subscription:sub-0\n"
    "message-id:test-dup\n"
    "content-length:180\n"
    "\n"
    '{"roundId":6841247,"currentDraw":"Heads","hasEnded":true,'
    '"millisLeft":2500,"totalMillis":3000,'
    '"messageType":"ROUND_GENERATED","timeStamp":1774517938000}'
)
assert collector.rounds_collected == 3, "Deduplication failed — duplicate was counted"
print("Test 5 PASSED: FlipCollector collects, deduplicates, and stops at target")

# --- Test 6: Verify database contents ---
from src.analyze import load_flips
df = load_flips(conn)
assert len(df) == 3, f"Expected 3 rows in DB, got {len(df)}"
assert list(df["outcome"]) == ["HEADS", "TAILS", "MIDDLE"]
assert list(df["round_id"]) == ["6841247", "6841248", "6841249"]
print("Test 6 PASSED: Database has correct data")

conn.close()
TEST_DB.unlink()

print("\n=== ALL BOT TESTS PASSED ===")
