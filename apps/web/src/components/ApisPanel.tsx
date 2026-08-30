"use client";

import { useCallback, useEffect, useState } from "react";
import type { Repository } from "@/lib/api";
import { fetchEndpoints, fetchFlows, type EndpointItem, type FlowSummary } from "@/lib/understanding";
import styles from "./ExplorePanel.module.css";

export function ApisPanel({
  repo,
  onAsk,
  onOpenFlow,
}: {
  repo: Repository;
  onAsk: (question: string) => void;
  onOpenFlow: (title: string) => void;
}) {
  const [groups, setGroups] = useState<Record<string, EndpointItem[]>>({});
  const [total, setTotal] = useState(0);
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [eps, fl] = await Promise.all([fetchEndpoints(repo.id), fetchFlows(repo.id)]);
      setGroups(eps.groups);
      setTotal(eps.total);
      setFlows(fl);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load APIs");
    }
  }, [repo.id]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>API map</h2>
          <p className={styles.hint}>
            Endpoints grouped by path prefix. Click one to open its handler flow.
            {total ? ` ${total} indexed.` : ""}
          </p>
        </div>
      </header>
      {error ? <div className={styles.error}>{error}</div> : null}
      {total === 0 ? (
        <p className={styles.muted}>
          No HTTP endpoints detected yet. Explain the repo from Overview, or this may be a library
          without routes.
        </p>
      ) : (
        Object.entries(groups)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([group, items]) => (
            <div key={group} className={styles.group}>
              <h3>{group}</h3>
              <ul className={styles.plainList}>
                {items.map((ep) => {
                  const title = `${ep.method} ${ep.path}`;
                  const hasFlow = flows.some((f) => f.title === title);
                  return (
                    <li key={`${ep.method}:${ep.path}:${ep.file_path || ""}`}>
                      <button
                        type="button"
                        className={styles.rowBtn}
                        onClick={() => {
                          if (hasFlow) onOpenFlow(title);
                          else onAsk(`What happens when ${title} is called?`);
                        }}
                      >
                        <strong>
                          <span className={styles.method}>{ep.method}</span> {ep.path}
                        </strong>
                        <span>
                          {ep.handler_name || ep.file_path || ep.source}
                          {hasFlow ? " · flow available" : ""}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
      )}
    </section>
  );
}
