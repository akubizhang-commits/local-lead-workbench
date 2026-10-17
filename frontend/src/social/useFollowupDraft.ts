import { useEffect, useState } from "react";

// Retain unsaved content during in-app navigation, scoped to its company.
// This is not durable storage and is cleared by a full page reload.
const windows = new WeakMap<Window, Map<string, {target: string; state: string; note: string}>>();
export function useFollowupDraft(followTarget?: { id: string }, companyId?: string) {
  let drafts = windows.get(window);
  if (!drafts) {drafts = new Map(); windows.set(window, drafts);}
  const saved = companyId ? drafts.get(companyId) : undefined;
  const [target, setTargetValue] = useState(saved?.target || ""),
    [state, setState] = useState(saved?.state || "draft"),
    [note, setNote] = useState(saved?.note || ""),
    [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (companyId) {
      if (note) drafts.set(companyId, {target, state, note});
      else drafts.delete(companyId);
    }
    const warn = (event: BeforeUnloadEvent) => {
      if (note.trim()) {event.preventDefault(); event.returnValue = "";}
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [companyId, target, state, note]);
  function setTarget(next: string) {
    if(next === target) return true;
    if(note.trim() && !window.confirm('当前内容尚未保存。切换联系对象将放弃这段内容，是否继续？')) return false;
    setTargetValue(next);
    setNote('');
    setState('draft');
    setConfirmed(false);
    return true;
  }
  useEffect(() => {
    if (followTarget) {
      if(!setTarget(followTarget.id)) return;
      setState("draft");
      setConfirmed(false);
      document
        .getElementById("social-followup")
        ?.scrollIntoView({ behavior: "smooth" });
    }
  }, [followTarget]);
  return {
    target,
    setTarget,
    state,
    setState,
    note,
    setNote,
    confirmed,
    setConfirmed,
  };
}
