import {useId, useState} from 'react';

/** Hover/focus previews a reason; click opens persistent, touch-friendly details. */
export function WorkflowHint({label, tip, expanded, controls, onToggle, warning = false}: {
 label:string;tip:string;expanded:boolean;controls:string;onToggle:()=>void;warning?:boolean;
}) {
 const id=useId();
 const [hovered,setHovered]=useState(false);
 const [focused,setFocused]=useState(false);
 const [dismissed,setDismissed]=useState(false);
 const visible=(hovered||focused)&&!dismissed&&!expanded;
 return <span className={`workflow-hint${warning?' is-warning':''}`}
  onMouseEnter={()=>{setHovered(true);setDismissed(false);}} onMouseLeave={()=>setHovered(false)}>
  <button type="button" aria-expanded={expanded} aria-controls={controls}
   aria-describedby={visible?id:undefined}
   onFocus={()=>{setFocused(true);setDismissed(false);}} onBlur={()=>{setFocused(false);setDismissed(false);}}
   onClick={()=>{setDismissed(true);onToggle();}}
   onKeyDown={event=>{if(event.key==='Escape'){setDismissed(true);if(expanded)onToggle();event.stopPropagation();}}}>
   <svg width="14" height="14" viewBox="0 0 20 20" fill="none" aria-hidden="true"><circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.5"/><path d="M10 9v5M10 6v.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg>
   {label}<span className="workflow-chevron" aria-hidden="true">{expanded?'−':'+'}</span>
  </button>
  <span className="workflow-tooltip" role="tooltip" id={id} hidden={!visible}>{tip}<span>点击查看详情</span></span>
 </span>;
}
