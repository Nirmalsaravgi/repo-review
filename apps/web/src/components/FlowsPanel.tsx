"use client";

import { useCallback, useEffect, useState } from "react";
import type { Repository } from "@/lib/api";
import { fetchBlastRadius, type BlastRadius } from "@/lib/graph";
import { fetchFlow, fetchFlows, githubBlob, type FlowDetail, type FlowSummary } from "@/lib/understanding";
import { FlowDiagram } from "./FlowDiagram";
import styles from "./ExplorePanel.module.css";

export function FlowsPanel({
  repo,
  onAsk,
  initialFlowId,
  initialTitle,
}: {
  repo: Repository;
  onAsk: (question: string, context?: string) => void;
  initialFlowId?: string | null;
  initialTitle?: string | null;
}) {
  const [flows, setFlows] = useState<FlowSummary[]>([]);
  const [active, setActive] = useState<FlowDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [blast, setBlast] = useState<BlastRadius | null>(null);

  const load = useCallback(async () => {
    try {
      const list = await fetchFlows(repo.id);
      setFlows(list);
      const preferred =
        (initialFlowId && list.find((f) => f.id === initialFlowId)) ||
        (initialTitle && list.find((f) => f.title === initialTitle)) ||
        list[0];
      if (preferred) setActive(await fetchFlow(repo.id, preferred.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load flows");
    }
  }, [repo.id, initialFlowId, initialTitle]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    setBlast(null);
    if (!active?.seed_symbol) return;
    void fetchBlastRadius(repo.id, active.seed_symbol)
      .then(setBlast)
      .catch(() => setBlast(null));
  }, [repo.id, active?.id, active?.seed_symbol]);

  async function open(id: string) {
    setError(null);
    try {
      setActive(await fetchFlow(repo.id, id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load flow");
    }
  }

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>How does it work?</h2>
          <p className={styles.hint}>
            Seeded from HTTP endpoints, jobs, webhooks, and events. Each flow is a deterministic
            call-graph walk, then explained in plain language.
          </p>
        </div>
      </header>
      {error ? <div className={styles.error}>{error}</div> : null}
      {flows.length === 0 ? (
        <p className={styles.muted}>
          No flows yet. On Overview, click <strong>Explain this repo</strong> (worker must be running).
        </p>
      ) : (
        <div className={styles.split}>
          <ul className={styles.plainList}>
            {flows.map((f) => (
              <li key={f.id}>
                <button
                  type="button"
                  className={styles.rowBtn}
                  data-active={active?.id === f.id}
                  onClick={() => void open(f.id)}
                >
                  <strong>{f.title}</strong>
                  <span>
                    {f.kind} · {f.step_count} steps
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {active ? (
            <div className={styles.detail}>
              <h3>{active.title}</h3>
              {active.explanation ? <p>{active.explanation}</p> : null}
              <FlowDiagram title={active.title} steps={active.steps} />
              {active.files.length > 0 ? (
                <p className={styles.files}>
                  {active.files.slice(0, 8).map((f) => (
                    <a key={f} href={githubBlob(repo, f)} target="_blank" rel="noreferrer">
                      {f}
                    </a>
                  ))}
                </p>
              ) : null}
              {blast ? (
                <div className={styles.impact}>
                  <span className={styles.risk} data-risk={blast.total > 8 ? "high" : "low"}>
                    What breaks
                  </span>
                  <p>
                    Changing {blast.symbol} may affect {blast.total} symbols
                    {blast.by_category.routes ? ` · ${blast.by_category.routes.length} routes` : ""}
                    {blast.by_category.tests ? ` · ${blast.by_category.tests.length} tests` : ""}.
                  </p>
                </div>
              ) : null}
              <button
                type="button"
                className={styles.primary}
                onClick={() =>
                  onAsk(`How does ${active.title} work end to end?`, `Selected flow: ${active.title}`)
                }
              >
                Explain in chat
              </button>
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}
