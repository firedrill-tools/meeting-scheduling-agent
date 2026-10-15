import argparse
import json
from pathlib import Path

from .agent import run_agent
from .calendar import CalendarStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthetic meeting scheduling agent")
    parser.add_argument("--db", type=Path, default=Path(".runs/calendar.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--fixture", type=Path, default=Path("fixtures/starter.json"))
    commands.add_parser("show")
    block = commands.add_parser("block")
    block.add_argument("person_id")
    block.add_argument("start_utc")
    block.add_argument("end_utc")
    run = commands.add_parser("run")
    run.add_argument("request")
    run.add_argument("--request-id", required=True)
    run.add_argument("--model")
    run.add_argument("--allow-book", action="store_true")
    args = parser.parse_args()
    store = CalendarStore(args.db)
    if args.command == "init":
        store.initialize(args.fixture)
        output = store.snapshot()
    elif args.command == "show":
        output = store.snapshot()
    elif args.command == "block":
        store.add_busy(args.person_id, args.start_utc, args.end_utc)
        output = store.snapshot()
    else:
        output = run_agent(store, args.request, args.request_id, args.model, args.allow_book)
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
