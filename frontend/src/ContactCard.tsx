import type {Contact} from './types';
import {Badge, External} from './ui';
import {date,label,tierClass,tierRank} from './helpers';
import {adoptionStatus, isHiddenContact} from './groupContacts';

export function ContactCard({contact: c}: {contact: Contact}) {
  if (isHiddenContact(c)) return null;
  const seen = new Set<string>();
  const sources = [...(c.sources || []), {source_url: c.source_url, evidence: c.evidence, observed_at: c.observed_at}]
    .filter(source => {
      if (!source.source_url && !source.evidence) return false;
      const key = JSON.stringify([source.source_url, source.evidence]);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  return <article className="contact-card">
    <div className="row between"><h3>{c.name?.trim() || '企业 / 部门联系方式'}</h3><div className="tags">{c.tier_label&&<span className={tierClass(tierRank(c.tier))}>{c.tier_label}</span>}{c.origin==='manual'&&<span className="badge badge-manual">人工录入</span>}<Badge value={adoptionStatus(c)}/><span className="badge" title="采集证据审核状态">证据：{label(c.review_status)}</span></div></div>
    <div className="contact-fields" aria-label={`${c.name || '企业'}的联系方式`}>
      {(c.fields || []).map(field => <div className="contact-field" key={JSON.stringify([field.key, field.value, field.source_url])}>
        <span className="contact-field-label">{field.label}：</span>
        <span className="contact-field-value">{field.key === 'url' ? <External url={field.value}/> : field.value}</span>
        <span className="contact-field-source"><External url={field.source_url}>{field.source_label}</External></span>
      </div>)}
    </div>
    {!c.fields?.length && <p className="muted">尚未提取到可展示的联系方式。</p>}
    <details className="contact-evidence"><summary>查看原文证据</summary>
      {sources.length ? sources.map(source => <div key={JSON.stringify([source.source_url, source.evidence])}>
        <blockquote>{source.evidence || '未提供证据片段'}</blockquote>
        <External url={source.source_url}>打开来源页面</External>
        <small className="block muted">采集于 {date(source.observed_at)}</small>
      </div>) : <blockquote>未提供证据片段</blockquote>}
    </details>
    <small className="block muted">采集于 {date(c.observed_at)} · 邮箱收信能力未验证</small>
  </article>;
}
