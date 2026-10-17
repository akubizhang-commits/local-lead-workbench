import { useFollowupDraft } from "./social/useFollowupDraft";
import { useState } from "react";
import { api } from "./api";
import { useResource } from "./hooks";
import { SocialDetail } from "./social/SocialDetail";
import { SocialEntry } from "./social/SocialEntry";
import { SocialFollowup } from "./social/SocialFollowup";
import { SocialList } from "./social/SocialList";
import type { Entry, SocialData } from "./social/types";
import type { Contact } from "./types";
// Preserve the public component imports used by existing callers.
export { SocialAccountList } from "./social/SocialAccountList";
export { SocialAccountSummary } from "./social/SocialAccountSummary";
export { SocialPagePreview } from "./social/SocialPagePreview";
export { SocialPosts } from "./social/SocialPosts";
export function SocialPanel({
  companyId,
  version,
  accountId,
  onOpenAccount,
  onBack,
  contacts = [],
}: {
  companyId: string;
  version: number;
  accountId?: string;
  onOpenAccount?: (id: string) => void;
  onBack?: () => void;
  contacts?: Contact[];
}) {
  const [revision, setRevision] = useState(0);
  const base = `/companies/${encodeURIComponent(companyId)}/social`;
  const { data, error, loading } = useResource<SocialData>(
    base,
    version + revision,
  );
  const [busy, setBusy] = useState(false),
    [failure, setFailure] = useState(""),
    [notice, setNotice] = useState("");
  const [followTarget, setFollowTarget] = useState<{ id: string }>();
  const followupDraft = useFollowupDraft(followTarget, companyId);
  async function act(
    work: () => Promise<unknown>,
    message: string | ((result: unknown) => string),
  ) {
    setBusy(true);
    setFailure("");
    setNotice("");
    try {
      const result = await work();
      setRevision((r) => r + 1);
      setNotice(typeof message === "function" ? message(result) : message);
    } catch (e) {
      setFailure(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }
  function decide(e: Entry, status: string, scope: string) {
    void act(
      () =>
        api(`${base}/${e.id}/decision`, "POST", {
          status,
          scope,
          revision: e.revision,
        }),
      "归属决定已保存",
    );
  }
  const render = (entry: Entry) => (
    <SocialEntry
      key={`${entry.id}:${entry.revision}`}
      entry={entry}
      pages={(data?.pages || []).filter((page) => page.entity_id === entry.id)}
      busy={busy}
      decide={decide}
      blocked={!!data?.do_not_contact}
      onFollow={(id) => setFollowTarget({ id })}
    />
  );
  const followup = data && (
    <SocialFollowup
      data={data}
      base={base}
      busy={busy}
      act={act}
      draft={followupDraft}
    />
  );
  if (accountId)
    return (
      <SocialDetail
        accountId={accountId}
        contacts={contacts}
        data={data}
        error={error}
        failure={failure}
        notice={notice}
        loading={loading}
        onBack={onBack}
        render={render}
        followup={followup}
      />
    );
  return (
    <SocialList
      companyId={companyId}
      base={base}
      contacts={contacts}
      data={data}
      error={error}
      failure={failure}
      notice={notice}
      loading={loading}
      busy={busy}
      act={act}
      onRefresh={() => setRevision((r) => r + 1)}
      onOpenAccount={onOpenAccount}
      render={render}
      followup={followup}
    />
  );
}
