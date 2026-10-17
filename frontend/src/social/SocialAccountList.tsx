import { contactChannels, contactRelation, contactTitle } from "../contactList";
import { date } from "../helpers";
import type { Contact } from "../types";
import { accountTitle, platformNames, scopeNames } from "./presentation";
import type { Entry, SocialPage } from "./types";
export function SocialAccountList({
  entries,
  pages,
  onOpen,
  contacts = [],
}: {
  entries: Entry[];
  pages: SocialPage[];
  onOpen: (id: string) => void;
  contacts?: Contact[];
}) {
  return (
    <div className="table-wrap">
      <table className="social-account-list">
        <thead>
          <tr>
            <th>联系人 / 账号</th>
            <th>联系渠道</th>
            <th>企业关系</th>
            <th title="最近成功读取账号资料的时间">更新时间</th>
          </tr>
        </thead>
        <tbody>
          {contacts.map((contact) => (
            <tr key={`contact:${contact.id}`}>
              <td>
                <button
                  className="company-link"
                  aria-label={`查看联系人 ${contactTitle(contact)}`}
                  onClick={() => onOpen(`contact:${contact.id}`)}
                >
                  {contactTitle(contact)}
                </button>
                {contact.name && contact.email && (
                  <small>{contact.email}</small>
                )}
              </td>
              <td>{contactChannels(contact)}</td>
              <td>{contactRelation(contact)}</td>
              <td>{date(contact.observed_at)}</td>
            </tr>
          ))}
          {entries.map((entry) => {
            const page = pages.find((p) => p.entity_id === entry.id);
            const updated =
              page?.content_observed_at ||
              (page?.status === "preview_read" ? page.observed_at : undefined);
            return (
              <tr key={entry.id}>
                <td>
                  <button
                    className="company-link"
                    onClick={() => onOpen(entry.id)}
                    aria-label={`查看账号 ${entry.label}`}
                  >
                    {accountTitle(entry, pages)}
                  </button>
                  {accountTitle(entry, pages) !== entry.label && (
                    <small>{entry.label}</small>
                  )}
                </td>
                <td>{platformNames[entry.platform] || entry.platform}</td>
                <td>
                  {entry.status === "confirmed"
                    ? scopeNames[entry.scope] || "已确认"
                    : entry.status === "excluded"
                      ? "不采用"
                      : "待确认"}
                </td>
                <td>{updated ? date(updated) : "暂无更新记录"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
