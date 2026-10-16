"""Noninteractive async native scheduling and model-free calendar operations."""
import argparse
import asyncio
from datetime import date
import json
import logging
from pathlib import Path
import signal
import sys

from .agent import run_agent_async
from .calendar import CalendarStore
from .datasets import initialize,initialize_fixture,list_cases,select
from .schemas import BookingScope


async def execute(store,request,request_id,scope,target,args):
    loop=asyncio.get_running_loop();task=asyncio.current_task();previous=signal.getsignal(signal.SIGTERM);loop.add_signal_handler(signal.SIGTERM,task.cancel)
    try:return await run_agent_async(store,request,request_id,args.model,args.allow_book,booking_scope=scope,allow_cancel=args.allow_cancel,cancel_event_id=target,base_url=args.base_url,timeout_seconds=args.timeout,output_root=args.output_root)
    finally:loop.remove_signal_handler(signal.SIGTERM);signal.signal(signal.SIGTERM,previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Synthetic timezone-aware scheduling and cancellation',allow_abbrev=False)
    parser.add_argument('--db',type=Path,default=Path('.runs/calendar.db'))
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('list-cases')
    init=commands.add_parser('init');group=init.add_mutually_exclusive_group();group.add_argument('--case');group.add_argument('--fixture',type=Path)
    commands.add_parser('show')
    block=commands.add_parser('block');block.add_argument('person_id');block.add_argument('start_utc');block.add_argument('end_utc')
    run=commands.add_parser('run');run.add_argument('request',nargs='?');run.add_argument('--case');run.add_argument('--request-id');run.add_argument('--model')
    run.add_argument('--allow-book',action='store_true');run.add_argument('--day',type=date.fromisoformat);run.add_argument('--participant',action='append');run.add_argument('--duration',type=int,default=30)
    run.add_argument('--allow-cancel',action='store_true');run.add_argument('--cancel-event-id');run.add_argument('--base-url',default='https://api.openai.com/v1');run.add_argument('--timeout',type=float,default=120);run.add_argument('--output-root',type=Path)
    args=parser.parse_args(argv)
    # This CLI owns its logging surface; the library does not alter host loggers.
    logging.getLogger('openai.agents').setLevel(logging.CRITICAL)
    try:
        if args.command=='list-cases':output=list_cases()
        elif args.command=='init':
            if args.fixture:
                store=initialize_fixture(args.fixture,args.db);output={'database_path':store.path,'state':store.snapshot()}
            else:output=initialize(args.case or 'starter',args.db)
        else:
            if not args.db.is_file():raise ValueError('calendar DB does not exist; initialize a fresh path first')
            store=CalendarStore(args.db)
            if args.command=='show':output=store.snapshot()
            elif args.command=='block':store.add_busy(args.person_id,args.start_utc,args.end_utc);output=store.snapshot()
            else:
                if args.case:
                    if args.request or args.request_id or args.day or args.participant or args.cancel_event_id or args.duration!=30:parser.error('a selected case supplies request identity, scope and target; choose write permission explicitly')
                    case,_,_=select(args.case);state=store.snapshot()
                    if state['dataset']!={'id':'synthetic-scheduling-cases','version':1,'profile':case['profile'],'case_id':case['id']}:raise ValueError('initialize the selected case into this DB before running it')
                    request=case['request'];request_id=case['request_id'];selection=case['selection'];scope=BookingScope.model_validate(selection['booking_scope']) if selection['booking_scope'] else None;target=selection['cancel_event_id']
                else:
                    if not args.request or not args.request_id:parser.error('supply a request and --request-id, or --case')
                    scope=None
                    if args.day or args.participant:
                        if not args.day or not args.participant:parser.error('scope requires --day and one or more --participant IDs')
                        scope=BookingScope(day=args.day,participant_ids=tuple(args.participant),duration_minutes=args.duration)
                    request=args.request;request_id=args.request_id;target=args.cancel_event_id
                output=asyncio.run(execute(store,request,request_id,scope,target,args))
        print(json.dumps(output,indent=2));return 0
    except (Exception,asyncio.CancelledError,KeyboardInterrupt) as error:
        source=error if getattr(error,'evidence',None) is not None else error.__context__
        print(json.dumps({'error':str(error) or type(error).__name__,'type':type(error).__name__,'receipt_path':getattr(source,'receipt_path',None),'evidence_error':getattr(source,'evidence',{}).get('evidence_error') if getattr(source,'evidence',None) else None}),file=sys.stderr)
        return 130 if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt)) else 1


if __name__=='__main__':raise SystemExit(main())
