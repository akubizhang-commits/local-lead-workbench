import type { useFollowupDraft } from "./useFollowupDraft";
import { api } from "../api";
import { date } from "../helpers";
import { External, Field } from "../ui";
import { platformNames, stateNames } from "./presentation";
import type { SocialData } from "./types";
export function SocialFollowup({
  data,
  base,
  busy,
  act,
  draft,
}: {
  data: SocialData;
  base: string;
  busy: boolean;
  act: (work: () => Promise<unknown>, message: string) => Promise<void>;
  draft: ReturnType<typeof useFollowupDraft>;
}) {
  const {
    target,
    setTarget,
    state,
    setState,
    note,
    setNote,
    confirmed,
    setConfirmed,
  } = draft;
  const followEntries = [...data.accounts, ...data.posts].filter(
    (e) => e.status === "confirmed",
  );
  return (
    <div id="social-followup" className="social-section">
      <h4>联系草稿与跟进</h4>
      <p className="muted">
        这里保存记录，不发送私信或评论。共享账号的其他企业跟进会一并显示，避免重复联系。
      </p>
      {!data.do_not_contact && followEntries.length > 0 ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (state !== "draft" && !confirmed) return;
            void act(
              async () => {
                await api(`${base}/outreach`, "POST", {
                  entity_id: target,
                  state,
                  note,
                });
                setNote("");
                setConfirmed(false);
              },
              state === "draft" ? "草稿已保存，尚未发送" : "跟进记录已保存",
            );
          }}
        >
          <div className="social-form-grid">
            <Field label="目标账号 / 帖子">
              <select
                required
                value={target}
                onChange={(e) => {
                  setTarget(e.target.value);
                }}
              >
                <option value="">请选择已确认的入口</option>
                {followEntries.map((e) => (
                  <option key={e.id} value={e.id}>
                    {platformNames[e.platform]} ·{" "}
                    {e.kind === "post" ? e.url : e.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="记录状态">
              <select
                value={state}
                onChange={(e) => {
                  setState(e.target.value);
                  setConfirmed(false);
                }}
              >
                {Object.entries(stateNames).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <Field label="草稿或跟进内容">
            <textarea
              required
              value={note}
              onChange={(e) => setNote(e.target.value)}
              maxLength={5000}
            />
          </Field>
          {state !== "draft" && (
            <label className="check-line">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              我确认此状态已实际发生
            </label>
          )}
          <button
            disabled={
              busy ||
              !target ||
              !note.trim() ||
              (state !== "draft" && !confirmed)
            }
            className="primary"
          >
            保存记录
          </button>
        </form>
      ) : (
        !data.do_not_contact && (
          <p className="muted">先确认一个账号或帖子的归属，即可记录跟进。</p>
        )
      )}
      {data.outreach.length ? (
        <div className="social-history">
          {data.outreach.map((o) => (
            <article key={o.id}>
              <div className="row between">
                <strong>人工记录 · {stateNames[o.state]}</strong>
                <time>{date(o.created_at)}</time>
              </div>
              <p>
                {o.company_name} ·{" "}
                <External url={o.url}>
                  {platformNames[o.platform]} · {o.label}
                </External>
              </p>
              <p className="pre-wrap">{o.note}</p>
            </article>
          ))}
        </div>
      ) : (
        <p className="muted">尚无跟进记录。</p>
      )}
    </div>
  );
}
