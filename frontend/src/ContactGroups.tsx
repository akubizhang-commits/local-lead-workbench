import type {Contact} from './types';
import {ContactCard} from './ContactCard';
import {groupContacts} from './groupContacts';

export function ContactGroups({contacts}: {contacts: Contact[]}) {
  const {procurement, channels, other, excluded, pending, irrelevant} = groupContacts(contacts);
  return <>
    <div className="section-title"><h3>采购联系人 <span className="count">{procurement.length}</span></h3></div>
    {procurement.length
      ? <div className="contact-grid">{procurement.map(c => <ContactCard key={c.id} contact={c}/>)}</div>
      : <p className="muted">暂未找到有明确采购岗位证据的联系人。</p>}
    {!!channels.length && <>
      <div className="section-title"><h3>采购部门 / 渠道 <span className="count">{channels.length}</span></h3></div>
      <div className="contact-grid">{channels.map(c => <ContactCard key={c.id} contact={c}/>)}</div>
    </>}
    {!!other.length && <details className="other-contacts">
      <summary>其他公开联系入口 <span className="count">{other.length}</span><span className="muted">尚无明确采购证据，可尝试联系；相同邮箱已合并</span></summary>
      <div className="contact-grid">{other.map(c => <ContactCard key={c.id} contact={c}/>)}</div>
    </details>}
    {!!irrelevant.length && <details className="other-contacts"><summary>非客户开发用途（{irrelevant.length}） · 不计入联系入口</summary><p className="muted">招聘、人力资源等明确无关的联系入口，保留原始来源备查。</p><div className="contact-grid">{irrelevant.map(c=><ContactCard key={c.id} contact={c}/>)}</div></details>}
    {!!pending.length && <details className="other-contacts pending-contacts"><summary>待确认联系人 <span className="count">{pending.length}</span><span className="muted">证据待审核，不计入可用采购或邮箱数量</span></summary><div className="contact-grid">{pending.map(c=><ContactCard key={c.id} contact={c}/>)}</div></details>}
    {!!excluded.length && <details className="other-contacts excluded-contacts"><summary>不采用的联系人 <span className="count">{excluded.length}</span><span className="muted">不计入采购与可用联系入口，不导出</span></summary><div className="contact-grid">{excluded.map(c=><ContactCard key={c.id} contact={c}/>)}</div></details>}
  </>;
}
