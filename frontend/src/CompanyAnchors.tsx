import {useEffect, useState} from 'react';

/** Section ids in page order (design §4.2). */
export const DETAIL_SECTIONS=['company-summary','company-contacts','company-facts','company-evidence','company-history'] as const;
export type DetailSection=typeof DETAIL_SECTIONS[number];
/** Offset below the sticky anchor bar: 48px bar + 8px gap; the top bar is not sticky (D18 A). */
export const ANCHOR_OFFSET=56;
/** The last section whose top (document coordinates) is at or above scrollY + offset; the first otherwise. */
export function activeSection(tops:Partial<Record<DetailSection,number>>,scrollY:number,offset:number):DetailSection {
 let current:DetailSection=DETAIL_SECTIONS[0];
 for(const id of DETAIL_SECTIONS){const top=tops[id];if(top!==undefined&&top<=scrollY+offset+1)current=id;}
 return current;
}
const reduceMotion=()=>typeof window.matchMedia==='function'&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
/** Heading that receives focus after a jump: the section title, or the summary of a collapsed section. */
function headingOf(section:HTMLElement):HTMLElement|null {
 return section.querySelector<HTMLElement>(':scope > summary, [data-anchor-heading]')||section.querySelector<HTMLElement>('h2,h3');
}
/** Open (for <details> sections), scroll to and focus a section; nothing happens when it is not rendered yet. */
export function jumpTo(id:DetailSection) {
 const section=document.getElementById(id);
 if(!section)return;
 if(section.tagName==='DETAILS')(section as HTMLDetailsElement).open=true;
 section.scrollIntoView({block:'start',behavior:reduceMotion()?'auto':'smooth'});
 headingOf(section)?.focus({preventScroll:true});
}
/** Sticky in-page navigation for company detail (P1-13): 概要 / 联系入口 / 资料 / 来源证据 / 历史. */
export function CompanyAnchors({contacts,evidence,history}:{contacts:number;evidence:number;history:number}) {
 const [active,setActive]=useState<DetailSection>(DETAIL_SECTIONS[0]);
 useEffect(()=>{
  if(typeof IntersectionObserver==='undefined')return;
  const update=()=>{
   const tops:Partial<Record<DetailSection,number>>={};
   for(const id of DETAIL_SECTIONS){const el=document.getElementById(id);if(el)tops[id]=el.getBoundingClientRect().top+window.scrollY;}
   setActive(activeSection(tops,window.scrollY,ANCHOR_OFFSET));
  };
  const observer=new IntersectionObserver(update,{rootMargin:`-${ANCHOR_OFFSET}px 0px -60% 0px`});
  for(const id of DETAIL_SECTIONS){const el=document.getElementById(id);if(el)observer.observe(el);}
  return()=>observer.disconnect();
 },[contacts,evidence,history]);
 const items:[DetailSection,string][]=[['company-summary','概要'],['company-contacts',`联系入口 ${contacts}`],['company-facts','资料'],
  ['company-evidence',`来源证据 ${evidence}`],['company-history',`历史 ${history}`]];
 return <nav className="detail-anchors" aria-label="页内导航">
  {items.map(([id,text])=><button key={id} type="button" aria-current={active===id?'true':undefined}
   onClick={()=>{setActive(id);jumpTo(id);}}>{text}</button>)}
 </nav>;
}
