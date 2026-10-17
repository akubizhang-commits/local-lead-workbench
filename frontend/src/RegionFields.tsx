import {useEffect, useId, useRef, useState} from 'react';
import {useResource} from './hooks';
import {Alert, Field} from './ui';
import type {Region, RegionConfig} from './types';

const same = (left:string, right:string) => left.trim().toLocaleLowerCase() === right.trim().toLocaleLowerCase();
const inState = (region:Region, stateId:string) =>
  (region.state_id || region.parent_id) === stateId;

function placeLabel(region:Region, fallbackType='城市 / 居民点') {
  const kind = region.subtype?.toLowerCase();
  const type = kind === 'cdp' ? 'CDP（人口普查指定地区）'
    : kind === 'incorporated_place' ? '建制市镇'
    : kind === 'local_municipality' ? '地方自治市'
    : kind === 'metropolitan_municipality' ? '大都市自治市' : fallbackType;
  const name = region.official_name && !same(region.name, region.official_name)
    ? `${region.name} — ${region.official_name}` : region.name;
  return `${name} · ${type}`;
}

function distinctPlaceLabels(regions:Region[], counties:Region[], fallbackType:string) {
  const labels = new Map(regions.map(region => [region.id, placeLabel(region, fallbackType)]));
  const countLabels = () => {
    const counts = new Map<string, number>();
    labels.forEach(label => {
      const key = label.trim().toLocaleLowerCase();
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    return counts;
  };
  const baseCounts = countLabels();
  const countyNames = new Map(counties.map(county => [county.id, county.official_name || county.name]));
  for (const region of regions) {
    const label = labels.get(region.id)!;
    if (baseCounts.get(label.trim().toLocaleLowerCase())! < 2) continue;
    const names = Array.from(new Set((region.county_ids || []).map(id => countyNames.get(id)).filter(Boolean))).sort();
    if (names.length) labels.set(region.id, `${label} · ${names.join(' / ')}`);
  }
  const remainingCounts = countLabels();
  for (const region of regions) {
    const label = labels.get(region.id)!;
    if (remainingCounts.get(label.trim().toLocaleLowerCase())! > 1) {
      labels.set(region.id, `${label} · ${region.geoid ? `GEOID ${region.geoid}` : `地区标识 ${region.id}`}`);
    }
  }
  return labels;
}

function uniqueMatch(regions:Region[], value:string, label?:(region:Region)=>string) {
  if (!value.trim()) return undefined;
  // A fully labelled suggestion distinguishes e.g. a city and a CDP sharing a name.
  const exact = label ? regions.filter(region => same(label(region), value)) : [];
  if (exact.length === 1) return exact[0];
  const matches = regions.filter(region => same(region.name, value)
    || !!region.official_name && same(region.official_name, value));
  return matches.length === 1 ? matches[0] : undefined;
}

// Used only when the configuration endpoint is unavailable or predates this UI.
const FALLBACK_CONFIGS:RegionConfig[] = [
  {country:'US',name:'美国',mode:'county_filter',labels:{state:'州 / 特区',county:'县 / 同级地区（可选）',place:'城市 / 居民点'},
    levels:['country','state','county','place'],scope:'whole_place',
    attribution:'美国人口普查局 · 2026年1月地区数据 · 50州及华盛顿特区',
    hint:'县筛选可选；跨县城市仍按整个城市 / 居民点检索，不裁剪市场范围。'},
  {country:'ZA',name:'南非',mode:'municipal_branch',labels:{state:'省',county:'地区 / 大都市自治市',place:'地方自治市'},
    levels:['country','state','county','place'],terminal_subtypes:['metropolitan_municipality'],scope:'whole_municipality',
    attribution:'南非国家财政部 · Municipal Money 自治市目录',
    hint:'大都市自治市选到第三级；其他地区继续选择地方自治市。自治市范围可能包含多个城镇，不等同于单个城市。'},
];
const MANUAL_LABELS = {state:'州 / 省',county:'地区',place:'城市 / 居民点'};

/** Modes supplied by the API determine optional filters versus municipal branches. */
export function RegionFields() {
  const id = useId();
  const [country, setCountry] = useState('US');
  const [manualCountry, setManualCountry] = useState(false);
  const [stateName, setStateName] = useState('');
  const [countyId, setCountyId] = useState('');
  const [city, setCity] = useState('');
  const [chosenState, setChosenState] = useState<Region>();
  const [chosenCounty, setChosenCounty] = useState<Region>();
  const [chosenPlace, setChosenPlace] = useState<Region>();
  const [chosenPlaceInput, setChosenPlaceInput] = useState('');
  const [retry, setRetry] = useState(0);
  const [validationError, setValidationError] = useState('');
  const formAnchor = useRef<HTMLInputElement>(null);
  const configuration = useResource<RegionConfig[]>('/region-configs', retry);
  const apiConfigs = Array.isArray(configuration.data) ? configuration.data.filter(config =>
    config && typeof config.country === 'string' && ['county_filter','municipal_branch'].includes(config.mode)
    && config.labels && ['state','county','place'].every(key => typeof config.labels[key as keyof RegionConfig['labels']] === 'string')) : [];
  const configs = apiConfigs.length ? apiConfigs : FALLBACK_CONFIGS;
  const config = configs.find(entry => entry.country === country);
  const labels = config?.labels || MANUAL_LABELS;
  const municipal = config?.mode === 'municipal_branch';
  const countyFilter = config?.mode === 'county_filter';
  const hasCountyLevel = !!config?.levels?.includes('county');
  const activeCountyId = hasCountyLevel ? countyId : '';
  const isTerminal = (region:Region) => (config?.terminal_subtypes || []).includes(region.subtype || '');
  const countries = useResource<Region[]>('/regions', retry);
  const states = useResource<Region[]>(/^[A-Z]{2}$/.test(country)
    ? `/regions?parent_id=${encodeURIComponent(country)}` : null, retry);
  const stateOptions = (states.data || []).filter(region => region.parent_id === country);
  const state = chosenState?.parent_id === country && uniqueMatch([chosenState], stateName)
    || uniqueMatch(stateOptions, stateName);
  const stateId = state?.id || '';
  const counties = useResource<Region[]>(hasCountyLevel && stateId
    ? `/regions?parent_id=${encodeURIComponent(stateId)}&kind=county` : null, retry);
  const countyOptions = (counties.data || []).filter(region => region.kind === 'county' && inState(region, stateId));
  const county = !hasCountyLevel ? undefined
    : chosenCounty?.id === activeCountyId && inState(chosenCounty, stateId) ? chosenCounty
    : countyOptions.find(region => region.id === activeCountyId);
  // A district's filtered local list must never replace the list of metros in
  // the third control. Keep the province-wide branch catalogue separately.
  const municipalPlaces = useResource<Region[]>(municipal && stateId
    ? `/regions?parent_id=${encodeURIComponent(stateId)}&kind=place` : null, retry);
  const places = useResource<Region[]>(stateId && config && (!municipal || county) && (!activeCountyId || county)
    ? `/regions?parent_id=${encodeURIComponent(stateId)}&kind=place${county ? `&county_id=${encodeURIComponent(county.id)}` : ''}`
    : null, retry);
  const metroOptions = (municipalPlaces.data || []).filter(region => region.kind === 'place' && inState(region, stateId) && isTerminal(region));
  const metro = municipal && !activeCountyId && chosenPlace && inState(chosenPlace, stateId) && isTerminal(chosenPlace) ? chosenPlace : undefined;
  const placeOptions = (places.data || []).filter(region =>
    region.kind === 'place' && inState(region, stateId)
    && (!municipal || !isTerminal(region) && !!county && !!region.county_ids?.includes(county.id))
    && (!county || !region.county_ids || region.county_ids.includes(county.id)));
  const placeLabels = distinctPlaceLabels(placeOptions, countyOptions, labels.place);
  const choiceLabel = (region:Region) => placeLabels.get(region.id) || placeLabel(region, labels.place);
  const retainedPlace = chosenPlace && inState(chosenPlace, stateId)
    && (!municipal || !isTerminal(chosenPlace) && !!county && !!chosenPlace.county_ids?.includes(county.id))
    && (!county || !chosenPlace.county_ids || chosenPlace.county_ids.includes(county.id))
    && same(chosenPlaceInput, city) ? chosenPlace : undefined;
  const place = metro || retainedPlace || uniqueMatch(placeOptions, city, choiceLabel);
  useEffect(() => {if (state) setChosenState(state);}, [state]);
  useEffect(() => {if (county) setChosenCounty(county);}, [county]);
  useEffect(() => {if (place) {setChosenPlace(place); setChosenPlaceInput(city);}}, [place, city]);
  useEffect(() => {
    if (!hasCountyLevel) {setCountyId(''); setChosenCounty(undefined);}
  }, [hasCountyLevel]);

  const invalidMunicipal = !municipal ? '' : !state ? `请选择有效${labels.state}`
    : !county && !metro ? `请选择${labels.county}`
    : !place ? `请选择有效${labels.place}` : '';
  // TaskForm is shared and owned elsewhere. Required controls handle ordinary
  // browser validation; this listener also protects direct/requested submits.
  useEffect(() => {
    const form = formAnchor.current?.form;
    if (!form || !invalidMunicipal) return;
    const guard = (event:Event) => {
      event.preventDefault(); event.stopPropagation();
      setValidationError(invalidMunicipal);
    };
    form.addEventListener('submit', guard, true);
    return () => form.removeEventListener('submit', guard, true);
  }, [invalidMunicipal]);
  function clearLower() {
    setCountyId(''); setCity(''); setChosenCounty(undefined); setChosenPlace(undefined);
    setChosenPlaceInput(''); setValidationError('');
  }
  function changeCountry(value:string) {
    setCountry(value.toUpperCase()); setStateName(''); setChosenState(undefined); clearLower();
  }
  function changeState(value:string) {
    setStateName(value); setChosenState(uniqueMatch(stateOptions, value)); clearLower();
  }
  function changeBranch(value:string) {
    clearLower();
    const district = countyOptions.find(region => region.id === value);
    if (district) {setCountyId(district.id); setChosenCounty(district); return;}
    const selectedMetro = metroOptions.find(region => region.id === value);
    if (selectedMetro) {
      setChosenPlace(selectedMetro); setCity(selectedMetro.name); setChosenPlaceInput(selectedMetro.name);
    }
  }
  const selectableCounties = county && !countyOptions.some(region => region.id === county.id) ? [county, ...countyOptions] : countyOptions;
  const selectableMetros = metro && !metroOptions.some(region => region.id === metro.id) ? [metro, ...metroOptions] : metroOptions;
  const regionErrors = [
    ['地区配置', configuration.error], ['国家', countries.error], [labels.state, states.error],
    [labels.county, counties.error], [labels.county, municipalPlaces.error], [labels.place, places.error],
  ].filter(([, error]) => error);
  return <>
    <Field label="国家">
      {apiConfigs.length > 0 ? <>
        <select name={manualCountry ? undefined : 'country'} value={manualCountry ? '__other' : country} required
          onChange={event => {
            const manual = event.target.value === '__other'; setManualCountry(manual);
            changeCountry(manual ? '' : event.target.value);
          }}>
          {configs.map(entry => <option key={entry.country} value={entry.country}>{entry.name}（{entry.country}）</option>)}
          <option value="__other">其他国家（手动输入代码）</option>
        </select>
        {manualCountry && <input name="country" aria-label="其他国家代码" required value={country} pattern="[A-Z]{2}" maxLength={2}
          placeholder="两位国家代码" onChange={event => changeCountry(event.target.value)}/>}
      </> : <>
        <input name="country" required value={country} placeholder="US" pattern="[A-Z]{2}" maxLength={2}
          title="两位大写国家代码" list={`${id}-countries`} onChange={event => changeCountry(event.target.value)}/>
        <datalist id={`${id}-countries`}>{countries.data?.map(region =>
          <option value={region.id} key={region.id}>{region.name}</option>)}</datalist>
      </>}
    </Field>
    <Field label={labels.state}>
      {municipal ? <select name="state" required value={state?.name || ''} onChange={event => changeState(event.target.value)}>
        <option value="">请选择{labels.state}</option>
        {(state && !stateOptions.some(region => region.id === state.id) ? [state, ...stateOptions] : stateOptions).map(region =>
          <option key={region.id} value={region.name}>{region.official_name || region.name}</option>)}
      </select> : <>
        <input name="state" required list={`${id}-states`} value={stateName} onChange={event => changeState(event.target.value)}/>
        <datalist id={`${id}-states`}>{stateOptions.map(region =>
          <option value={region.name} key={region.id}>{region.official_name || region.name}{region.subtype === 'federal_district' ? ' · 华盛顿特区' : ''}</option>)}</datalist>
      </>}
      {states.loading && <small role="status">正在读取{labels.state}…</small>}
    </Field>
    {hasCountyLevel && <Field label={labels.county}>
      <select name={municipal ? 'municipal_branch' : 'county_id'} required={municipal}
        value={metro?.id || county?.id || ''}
        disabled={!stateId || (municipal ? counties.loading && municipalPlaces.loading : counties.loading || !!counties.error)}
        onChange={event => changeBranch(event.target.value)}>
        <option value="">{municipal ? `请选择${labels.county}` : '全州城市（不按县筛选）'}</option>
        {municipal ? <>
          <optgroup label="地区">{selectableCounties.map(region =>
            <option key={region.id} value={region.id}>{region.official_name || region.name}</option>)}</optgroup>
          <optgroup label="大都市自治市">{selectableMetros.map(region =>
            <option key={region.id} value={region.id}>{region.official_name || region.name}</option>)}</optgroup>
        </> : selectableCounties.map(region => <option key={region.id} value={region.id}>{region.official_name || region.name}</option>)}
      </select>
      {(counties.loading || municipal && municipalPlaces.loading) && <small role="status">正在读取{labels.county}…</small>}
    </Field>}
    {(!municipal || activeCountyId) && <Field label={labels.place}>
      {municipal ? <select name="city" required value={place?.id || ''}
        onChange={event => {
          const selected = placeOptions.find(region => region.id === event.target.value);
          setChosenPlace(selected); setCity(selected ? choiceLabel(selected) : '');
          setChosenPlaceInput(selected ? choiceLabel(selected) : ''); setValidationError('');
        }}>
        <option value="">请选择{labels.place}</option>
        {(place && !placeOptions.some(region => region.id === place.id) ? [place, ...placeOptions] : placeOptions).map(region =>
          <option key={region.id} value={region.id}>{choiceLabel(region)}</option>)}
      </select> : <>
        <input name="city" required list={`${id}-places`} value={city} onChange={event => {
          setCity(event.target.value); setChosenPlace(uniqueMatch(placeOptions, event.target.value, choiceLabel));
          setChosenPlaceInput(event.target.value); setValidationError('');
        }}/>
        <datalist id={`${id}-places`}>{placeOptions.map(region =>
          <option key={region.id} value={choiceLabel(region)} label={choiceLabel(region)}>{choiceLabel(region)}</option>)}</datalist>
      </>}
      {places.loading && <small role="status">正在读取{labels.place}…</small>}
      {place && <small>正式名称：{place.official_name || place.name}</small>}
    </Field>}
    <input ref={formAnchor} type="hidden" name="canonical_state" value={state?.name || (municipal ? '' : stateName.trim())}/>
    <input type="hidden" name="canonical_city" value={place?.name || (municipal ? '' : city.trim())}/>
    {state && <input type="hidden" name="state_id" value={state.id}/>}
    {county && (municipal || counties.loading || !!counties.error) && <input type="hidden" name="county_id" value={county.id}/>}
    {county && <input type="hidden" name="county" value={county.name}/>}
    {place && <input type="hidden" name="place_id" value={place.id}/>}
    <div className="wide">
      {config?.attribution && <p className="muted">{config.attribution}</p>}
      {config?.hint && <p className="muted">{config.hint}</p>}
      {countyFilter && hasCountyLevel && <details>
        <summary>{config.name}地区层级说明</summary>
        <p className="muted">县仅用于筛选，不要求严格的四级层级。建制市镇与 CDP 分别标示；CDP 为人口普查指定地区。</p>
        <p className="muted">可手动输入未收录地区，作为未验证范围；按县筛选时须从建议中选择有效居民点。切换上级地区会清空下级选择。</p>
      </details>}
      {!municipal && ((!state && stateName.trim()) || (!place && city.trim())) && <p className="muted">
        {county ? '已选县，请从建议中选择有效居民点；手填地区需先取消县筛选。' : '当前地区未验证或同名未区分，可使用文字检索；选择完整建议可关联地区标识。'}
      </p>}
      {validationError && <Alert>{validationError}</Alert>}
      {regionErrors.map(([name, error], index) => <Alert key={`${name}:${index}`}>{name}建议暂不可用：{error}</Alert>)}
      {!!regionErrors.length && <button type="button" onClick={() => setRetry(value => value + 1)}>重试地区建议</button>}
    </div>
  </>;
}
