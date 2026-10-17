"""Read-only repository audit; all Store instances use newly created /tmp dirs.
Run: PYTHONDONTWRITEBYTECODE=1 <repo>/.venv/bin/python -B /tmp/lead-architecture-audit.py
No network, API app, browser, crawler, real database or worker service is started.
"""
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, '/Users/zhangnu/outputs/local-lead-workbench')
import asyncio, io, json, tempfile
from pathlib import Path
from openpyxl import load_workbook
from leadworkbench.db import Store
from leadworkbench.sources.maps import _record
from leadworkbench.worker import process_run

results = {}
def setup(label):
    path = Path(tempfile.mkdtemp(prefix='lead-audit-'+label+'-', dir='/tmp'))
    s = Store(path)
    m = s.create_market(dict(name='Audit',country='US',state='NY',city='New York',company_types=['retail'],keywords=[],auto_enrich=False))
    return s,m,s.start_task(m['id'])
def rec(address='1 Old Road New York',phone='12125550111'):
    return dict(source_key='google_maps:cid:123',source_url='https://www.google.com/maps?cid=123',name='Audit Brand',website='https://audit.example',address=address,phone=phone)
def ingest(s,m,r,record):
    return s.ingest_market(record,r['id'],'synthetic query',m['id'])

# 1: Same place_id and URL acquire a CID on a later observation after moving.
s,m,r=setup('identity')
raw=dict(title='Audit Brand',link='https://www.google.com/maps/search/?api=1&query_place_id=PLACE_A',place_id='PLACE_A',web_site='https://audit.example',address='1 Old Road New York',phone='12125550111')
a=_record(raw,'2026-09-20T00:00:00+00:00')
b=_record({**raw,'cid':'123','address':'2 New Road New York','phone':'12125550222'},'2026-09-21T00:00:00+00:00')
x=ingest(s,m,r,a); y=ingest(s,m,r,b)
assert x['id'] != y['id'] and len(s.companies())==2 and not s.reviews()
results['identity_promotion_move']={'keys':[a['source_key'],b['source_key']], 'same_url':a['source_url']==b['source_url'],'companies':len(s.companies()),'pending_reviews':len(s.reviews()),'addresses':[c['address'] for c in s.companies()]}

# 2: Human acceptance is not persisted as a protected field selection.
s,m,r=setup('accept')
c=ingest(s,m,r,rec())
s.apply_enrichment(c['id'],dict(status='completed',observations=[dict(field='phone',value='12125550222',source_url='https://audit.example/contact',evidence='Public phone')]))
review=next(v for v in s.reviews() if v['field']=='phone')
s.review_decision(review['id'],'accept')
before=s.company(c['id'])
ingest(s,m,r,rec(phone='12125550333'))
after=s.company(c['id'])
assert before['phone']=='12125550222' and after['phone']=='12125550333' and not s.reviews()
results['accepted_field_overwritten']={'accepted':before['phone'],'overrides':before['overrides'],'after_maps':after['phone'],'pending_reviews':len(s.reviews())}

# 3: A rejected contact is reset to pending and exported by another role change.
s,m,r=setup('reject')
c=s.manual_company('Audit','https://audit.example')
def enrich(role):
    s.apply_enrichment(c['id'],dict(status='completed',contacts=[dict(name='Pat',email='pat@audit.example',role=role,kind='person',source_url='https://audit.example/team',evidence='Public team card')]))
enrich('Buyer'); enrich('Director')
s.review_decision(s.reviews()[0]['id'],'reject')
from leadworkbench.export import export_data
def export_contacts():
    return list(load_workbook(io.BytesIO(export_data(s,'xlsx')))['Contacts'].values)[1:]
assert not export_contacts()
enrich('Sales')
contact=s.detail(c['id'])['contacts'][0]
assert contact['review_status']=='pending' and len(export_contacts())==1
results['rejected_contact_returns']={'after_reject_export_count':0,'after_role_change_review_status':contact['review_status'],'after_role_change_export_count':len(export_contacts()),'role':contact['role']}

# 4: Source budget is per request; retries cannot advance a saturated terminal tile.
async def coverage():
    s,m,r=setup('coverage')
    calls=[]
    job=dict(key='dense-leaf',query='retailer in New York',geo=[40.75,-73.95],bbox=[40.7,40.8,-74,-73.9],zoom=17,depth=10,level=3)
    async def planner(*args):
        return dict(status='completed',jobs=[job],coverage={},warnings=[])
    async def discover(query,output_dir,**kw):
        calls.append({k:v for k,v in kw.items() if k!='should_stop'})
        # Deterministic source has 121 matches; adapter budget exposes the first 120.
        rows=[{**rec(), 'source_key':f'fixture:{i}','name':f'Company {i}','source_url':f'https://www.google.com/maps?cid={i+1}'} for i in range(121)]
        return dict(status='completed',records=rows[:kw['limit']],saturated=True,stop_reason='company_limit',warnings=[])
    await process_run(s,r['id'],planner=planner,discover=discover)
    s.control(r['id'],'resume');s.recover()
    await process_run(s,r['id'],planner=planner,discover=discover)
    new=s.start_task(m['id'])
    await process_run(s,new['id'],planner=planner,discover=discover)
    assert len(s.companies())==120 and len(calls)==3 and calls[0]==calls[1]==calls[2]
    results['coverage_no_forward_progress']={'source_matches':121,'stored':len(s.companies()),'calls':len(calls),'same_request_parameters':True,'latest_status':s.run(new['id'])['status'],'coverage':s.task(m['id'])['coverage']}
asyncio.run(coverage())

# 5: A past paused run consumes a new scheduled occurrence and remains paused.
s,m,r=setup('schedule')
s.control(r['id'],'pause')
schedule=s.create_schedule(task_id=m['id'],kind='discovery',interval_months=1)
with s.connect() as con:
    con.execute("UPDATE schedules SET anchor='2026-01-01T00:00:00+00:00',next_due_at='2026-02-01T00:00:00+00:00' WHERE id=?",(schedule['id'],))
s.recover();s.dispatch_due()
with s.connect() as con:
    occurrence=dict(con.execute('SELECT * FROM occurrences').fetchone())
assert occurrence['run_id']==r['id'] and not s.active_runs() and s.run(r['id'])['status']=='paused'
s.control(r['id'],'cancel');s.dispatch_due()
assert not s.active_runs()
results['scheduled_occurrence_consumed']={'occurrence_reuses_paused_run':True,'active_runs_after_dispatch':0,'next_due_at':s.schedules()[0]['next_due_at'],'active_runs_after_cancel_and_redispatch':0}
print(json.dumps(results,ensure_ascii=False,indent=2))
Path('/tmp/lead-architecture-audit-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
