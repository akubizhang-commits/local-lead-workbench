import type { ReactNode } from "react";
import { ContactCard } from "../ContactCard";
import type { Contact } from "../types";
import { Alert } from "../ui";
import { accountTitle } from "./presentation";
import type { Entry, SocialData } from "./types";
export function SocialDetail({
  accountId,
  contacts,
  data,
  error,
  failure,
  notice,
  loading,
  onBack,
  render,
  followup,
}: {
  accountId: string;
  contacts: Contact[];
  data?: SocialData;
  error: string;
  failure: string;
  notice: string;
  loading: boolean;
  onBack?: () => void;
  render: (entry: Entry) => ReactNode;
  followup: ReactNode;
}) {
  const selectedContact = accountId?.startsWith("contact:")
    ? contacts.find((c) => c.id === accountId.slice(8))
    : undefined;
  if (accountId?.startsWith("contact:"))
    return (
      <section className="panel">
        <div className="section-title">
          <h3>联系人详情</h3>
          <button onClick={onBack}>← 返回企业详情</button>
        </div>
        {selectedContact ? (
          <ContactCard contact={selectedContact} />
        ) : (
          <p>此联系人已更新或不再显示，请返回企业详情查看。</p>
        )}
      </section>
    );
  const selectedAccount = data
    ? [...data.accounts, ...data.posts, ...data.excluded].find(
        (entry) => entry.id === accountId,
      )
    : undefined;
  return (
    <section className="panel social-panel">
      <div className="section-title">
        <h3>
          {selectedAccount
            ? accountTitle(selectedAccount, data?.pages || [])
            : "账号详情"}
        </h3>
        <button onClick={onBack}>← 返回企业详情</button>
      </div>
      {error && <Alert>{error}</Alert>}
      {failure && <Alert>{failure}</Alert>}
      {notice && <p role="status">{notice}</p>}
      {loading && <p>正在读取账号资料…</p>}
      {selectedAccount
        ? render(selectedAccount)
        : !loading && data && <p>此账号未关联当前企业或记录已移除。</p>}
      {data && followup}
    </section>
  );
}
