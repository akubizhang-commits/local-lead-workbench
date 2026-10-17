"""Build a versioned local US gazetteer from public Census TIGERweb layers.
Import-time dependencies: shapely>=2 (not required by the application).
Run: .venv/bin/python scripts/import_us_geography.py
Source geometries are cached under /tmp; output is atomically replaced only after validation.
"""
from pathlib import Path
import concurrent.futures,datetime,gzip,hashlib,json,time,urllib.parse,subprocess
ROOT=Path(__file__).resolve().parents[1]
CACHE=Path('/tmp/us-geography-2026');CACHE.mkdir(exist_ok=True)
BASE='https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/'
LAYERS={'states':('State_County',0),'counties':('State_County',1),'incorporated':('Places_CouSub_ConCity_SubMCD',4),'cdp':('Places_CouSub_ConCity_SubMCD',5)}

def request(url):
    last=None
    for attempt in range(4):
        try:
            result=subprocess.run(['curl','-sSL','--fail','--connect-timeout','15','--max-time','90',url],capture_output=True,check=True)
            data=json.loads(result.stdout)
            if 'error' in data:raise ValueError(data['error'])
            return data
        except Exception as exc:
            last=exc;time.sleep(2*(attempt+1))
    raise last

def fetch_features(url, params):
    """A large geometry page can time out; split without dropping any offsets."""
    size=int(params['resultRecordCount'])
    try:
        data=request(url+'/query?'+urllib.parse.urlencode(params))
        if 'features' not in data:raise ValueError('No feature array in source response')
        return data
    except Exception:
        if size<=25:raise
        left=size//2
        a=fetch_features(url,{**params,'resultRecordCount':left})
        b=fetch_features(url,{**params,'resultOffset':int(params['resultOffset'])+left,'resultRecordCount':size-left})
        return {'type':'FeatureCollection','features':a['features']+b['features']}

def layer(name):
    service,lid=LAYERS[name];url=f'{BASE}{service}/MapServer/{lid}'
    metadata=request(url+'?f=json')
    if 'January 1, 2026' not in metadata.get('description',''):
        raise ValueError(f'{name}: source vintage changed; select a matching version before importing')
    count=request(url+'/query?where=1%3D1&returnCountOnly=true&f=json')['count']
    print(name,'count',count,flush=True)
    rows=[]
    for offset in range(0,count,250):
        path=CACHE/f'{name}-{offset}.json.gz'
        if path.exists():
            with gzip.open(path,'rt') as f:data=json.load(f)
        else:
            params={'f':'geojson','where':'1=1','outFields':'*','returnGeometry':'false' if name=='states' else 'true','outSR':'4326','orderByFields':'OBJECTID','resultOffset':offset,'resultRecordCount':250}
            # The final 2026 incorporated-place pages contain unusually large
            # geometries. Fetch them in smaller pieces from the outset.
            if name=='incorporated' and offset>=19250:
                data={'type':'FeatureCollection','features':[]}
                for start in range(offset,min(offset+250,count),50):
                    part_path=CACHE/f'{name}-part-{start}.json.gz'
                    if part_path.exists():
                        with gzip.open(part_path,'rt') as f:part=json.load(f)
                    else:
                        part=fetch_features(url,{**params,'resultOffset':start,'resultRecordCount':min(50,count-start)})
                        partial=part_path.with_suffix('.partial')
                        with gzip.open(partial,'wt') as f:json.dump(part,f)
                        partial.replace(part_path)
                    data['features'].extend(part['features'])
                    print(name,'small page',start,len(part['features']),flush=True)
            else:
                data=fetch_features(url,params)
            partial=path.with_suffix('.partial')
            with gzip.open(partial,'wt') as f:json.dump(data,f)
            partial.replace(path)
        rows.extend(data['features'])
        print(name,len(rows),'/',count,flush=True)
    if len(rows)!=count:raise ValueError(f'{name} truncated {len(rows)} != {count}')
    digests={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(CACHE.glob(name+'-*.json.gz'))}
    return rows,{'url':url,'description':metadata.get('description'),'source_count':count,'downloaded_count':len(rows),'source_pages_sha256':digests}

def build():
    from shapely.geometry import shape
    from shapely import make_valid,STRtree
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results=dict(zip(LAYERS,pool.map(layer,LAYERS)))
    rawstates,meta=results['states']
    states=[];bycode={}
    for f in rawstates:
        p=f['properties'];code=p['STATE']
        if int(code)>56:continue
        r={'id':'US-'+p['STUSAB'],'name':p['NAME'],'parent_id':'US','kind':'state','geoid':code,'subtype':'federal_district' if code=='11' else 'state'}
        states.append(r);bycode[code]=r
    assert len(states)==51
    counties=[];countyshapes=[]
    for f in results['counties'][0]:
        p=f['properties']
        if p['STATE'] not in bycode:continue
        counties.append({'id':'US-COUNTY-'+p['GEOID'],'name':p['NAME'],'parent_id':bycode[p['STATE']]['id'],'state_id':bycode[p['STATE']]['id'],'kind':'county','geoid':p['GEOID'],'subtype':p['LSADC'],'official_name':p['NAME']})
        countyshapes.append(make_valid(shape(f['geometry'])))
    tree=STRtree(countyshapes)
    places=[]
    for key in ('incorporated','cdp'):
        for f in results[key][0]:
            p=f['properties']
            if p['STATE'] not in bycode:continue
            geo=make_valid(shape(f['geometry']));links=[]
            for index in tree.query(geo,predicate='intersects'):
                county=counties[index]
                if county['state_id']!=bycode[p['STATE']]['id']:continue
                # Ignore zero-area boundary touching and floating-point slivers.
                if geo.intersection(countyshapes[index]).area>1e-10:links.append(county['id'])
            places.append({'id':'US-PLACE-'+p['GEOID'],'name':p['BASENAME'],'official_name':p['NAME'],'parent_id':bycode[p['STATE']]['id'],'state_id':bycode[p['STATE']]['id'],'kind':'place','subtype':'incorporated_place' if key=='incorporated' else 'cdp','geoid':p['GEOID'],'county_ids':sorted(links),'geo':[float(p['INTPTLAT']),float(p['INTPTLON'])],'bbox':[geo.bounds[1],geo.bounds[3],geo.bounds[0],geo.bounds[2]]})
    assert len({r['id'] for r in places})==len(places)
    assert len(places)>30000 and len(counties)>3000
    missing=[r['id'] for r in places if not r['county_ids']]
    if missing:raise ValueError(f'Unmatched places: {missing}')
    out={'metadata':{'downloaded_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'50 US states and District of Columbia; territories excluded','vintage':'2026-01-01','sources':{k:v[1] for k,v in results.items()},'county_relation':'Computed positive-area intersection of matching-vintage Census boundaries; county membership is many-to-many, not a legal parent tree. Area cutoff 1e-10 square degrees excludes boundary-only touches and numerical slivers.','counts':{'states':len(states),'counties':len(counties),'places':len(places),'multi_county_places':sum(len(r['county_ids'])>1 for r in places)}},'regions':[{'id':'US','name':'美国','parent_id':None,'kind':'country'},*sorted(states,key=lambda r:r['name']),*sorted(counties,key=lambda r:(r['parent_id'],r['name'])),*sorted(places,key=lambda r:(r['parent_id'],r['name'],r['id']))]}
    target=ROOT/'leadworkbench/datasets/us-geography-2026.json.gz';tmp=target.with_suffix('.tmp')
    with gzip.open(tmp,'wt',encoding='utf-8') as f:json.dump(out,f,ensure_ascii=False,separators=(',',':'))
    tmp.replace(target)
    print(json.dumps(out['metadata']['counts']),flush=True)

if __name__=='__main__':build()
