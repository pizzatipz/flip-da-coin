"""Allow running as `python -m src <command> [options]`."""
import sys

COMMANDS = {
    "bot":        "Collect data by observing coin flips",
    "analyze":    "Run statistical analysis on collected data",
    "strategies": "Run strategy backtesting on collected data",
}

if len(sys.argv) < 2:
    print("Usage: python -m src <command> [options]\n")
    print("Commands:")
    for cmd, desc in COMMANDS.items():
        print(f"  {cmd:<12s}  {desc}")
    sys.exit(1)

command = sys.argv.pop(1)

if command == "bot":
    from src.bot import main
    main()
elif command == "analyze":
    from src.analyze import main
    main()
elif command == "strategies":
    from src.strategies import main
    main()
else:
    print(f"Unknown command: {command}")
    print(f"Available: {', '.join(COMMANDS)}")
    sys.exit(1)
