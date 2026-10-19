import type {Company} from './types';

/** "企业 / 地区" cell: the address city when parsed, otherwise the target city (design §5.4). */
export function ActualCityCell({company}: {company: Pick<Company, 'city' | 'country' | 'actual_city' | 'actual_city_basis' | 'nearby'>}) {
  const parsed = company.actual_city_basis === 'address' && !!company.actual_city;
  const shown = parsed ? company.actual_city : company.city;
  return <>
    <small>{[shown, company.country].filter(Boolean).join(' / ') || '地区未记录'}
      {parsed && company.nearby && <> <span className="badge badge-nearby"
        title={`地址在 ${company.actual_city}，不在目标城市 ${company.city}`}>邻近城市</span></>}
    </small>
    {company.actual_city_basis === 'target' && company.city && <small className="muted">按目标城市显示（地址未解析）</small>}
  </>;
}

/** Detail-page city line. */
export function ActualCityLine({company}: {company: Company}) {
  if (!company.actual_city_basis) return null;
  if (company.actual_city_basis !== 'address') {
    return <p className="muted">实际城市：未能从地址解析，暂按目标城市 {company.city || '（未记录）'} 显示</p>;
  }
  return <p>实际城市：{company.actual_city}（按地址解析）{company.city && ` · 目标城市：${company.city}`}
    {company.nearby && <> <span className="badge badge-nearby">邻近城市</span></>}</p>;
}
