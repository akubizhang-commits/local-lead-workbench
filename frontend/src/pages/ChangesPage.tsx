import { useResource } from "../hooks";
import type { Change } from "../types";

import { Resource } from "../Resource";
import { ChangeList } from "./ChangeList";
export function ChangesPage({
  version,
  refresh,
  openCompany,
}: {
  version: number;
  refresh: () => void;
  openCompany: (id: string) => void;
}) {
  const changes = useResource<Change[]>("/changes", version);
  return (
    <Resource {...changes} retry={refresh}>
      <section className="panel">
        <ChangeList items={changes.data || []} onOpen={openCompany} />
      </section>
    </Resource>
  );
}
