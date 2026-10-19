"""Fresh locked wheel and source installs outside the checkout."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
import tomllib
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
UV=os.environ.get('UV') or shutil.which('uv')
if not UV:raise RuntimeError('uv is required for distribution verification')
VERSION=tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
wheel=ROOT/'dist'/f'meeting_scheduling_agent-{VERSION}-py3-none-any.whl';sdist=ROOT/'dist'/f'meeting_scheduling_agent-{VERSION}.tar.gz'
env={**os.environ}
for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','MEETING_MODEL','VIRTUAL_ENV','PYTHONPATH','PYTHONHOME'):env.pop(key,None)


def run(command,cwd,check=True):return subprocess.run(command,cwd=cwd,env=env,text=True,capture_output=True,check=check,timeout=180)


def exercise(python,base):
    cli=[str(python),'-I','-m','meeting_scheduler.cli'];run([str(python.parent/'schedule-meeting'),'--help'],base)
    manifest=json.loads(run([*cli,'list-cases'],base).stdout);assert len(manifest['cases'])==10 and manifest['version']==1
    for case in manifest['cases']:
        path=base/f'{case["id"]}.db';result=json.loads(run([*cli,'--db',str(path),'init','--case',case['id']],base).stdout)
        assert result['fixture_sha256']==case['sha256'] and path.stat().st_mode&0o777==0o600
    failed=run([*cli,'--db',str(base/'starter.db'),'run','--case','starter','--model','gpt-4.1'],base,False);assert failed.returncode==1
    error=json.loads(failed.stderr);receipt=json.loads(Path(error['receipt_path']).read_text());assert receipt['status']=='failed' and receipt['result'] is None and receipt['provider_observations']==[]
    program='''
import sys
from pathlib import Path
import meeting_scheduler
from meeting_scheduler.calendar import CalendarStore
assert Path(meeting_scheduler.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
base=Path(sys.argv[1]);busy=CalendarStore(base/'busy.db').snapshot()
assert len(busy['people'])==12 and len(busy['meetings'])==18 and len(busy['busy'])==54
assert len(busy['outbox'])==12 and len(busy['cancellations'])==3 and len(busy['cancellation_outbox'])==2
assert all(m['origin']=='human_import' for m in busy['meetings'])
store=CalendarStore(base/'starter.db');offered=store.list_slots('2026-11-05',['mira','leo'],30)
assert '2026-11-05T15:00:00Z' in offered['slots_utc']
event=store.book('installed-book','2026-11-05T15:00:00Z',30,['mira','leo'],offered['revision']);store.confirm(event['event_id'],request_id='installed-book')
store.cancel('installed-cancel',event['event_id'],1,'Synthetic human package check.');store.notify_cancellation(event['event_id'],request_id='installed-cancel')
assert store.book('installed-book','2026-11-05T15:00:00Z',30,['leo','mira'],0)['status']=='cancelled'
state=store.snapshot();assert state['revision']==2 and len(state['outbox'])==1 and len(state['cancellation_outbox'])==1
recovery=CalendarStore(base/'cancel-recovery.db');target=recovery.snapshot()['meetings'][0]['event_id']
assert recovery.inspect('recover-cancel',cancel_event_id=target)['target_cancellation']['reason']=='Requester cancelled.'
'''
    run([str(python),'-I','-c',program,str(base)],base)


with TemporaryDirectory(prefix='scheduling-distribution-') as directory:
    temp=Path(directory)
    with ZipFile(wheel) as archive:
        assert 'meeting_scheduler/sample_data/manifest.json' in archive.namelist()
        assert len([name for name in archive.namelist() if name.startswith('meeting_scheduler/sample_data/') and name.endswith('.json')])==11
    requirements=temp/'requirements.txt';requirements.write_text(run([UV,'export','--locked','--no-dev','--no-emit-project','--format','requirements-txt'],ROOT).stdout)
    wheel_env=temp/'wheel-env';run([UV,'venv','--python',sys.executable,str(wheel_env)],temp);python=wheel_env/'bin/python'
    run([UV,'pip','sync','--python',str(python),str(requirements)],temp);run([UV,'pip','install','--python',str(python),'--no-deps',str(wheel)],temp)
    wheel_case=temp/'wheel-case';wheel_case.mkdir();exercise(python,wheel_case)
    extracted=temp/'source';extracted.mkdir()
    with tarfile.open(sdist) as archive:archive.extractall(extracted,filter='data')
    source,=extracted.iterdir();assert (source/'uv.lock').exists();run([UV,'sync','--locked','--no-dev','--no-editable','--project',str(source)],temp)
    assert (source/'firedrill/scenario.json').exists() and not (source/'firedrill/node_modules').exists()
    run(['npm','ci','--prefix','firedrill','--ignore-scripts','--no-audit','--no-fund'],source)
    run(['npm','test','--prefix','firedrill'],source)
    source_case=temp/'source-case';source_case.mkdir();exercise(source/'.venv/bin/python',source_case)
print('Fresh locked wheel and sdist verified outside checkout: console/module CLI, ten private calendars, key-preflight receipts, real busy state, booking/confirmation/cancellation and exact recovery. No live model or email-delivery claim.')
