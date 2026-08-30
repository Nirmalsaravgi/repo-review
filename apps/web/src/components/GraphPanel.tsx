"use client";

import { useCallback, useEffect, useState } from "react";
import type { Repository } from "@/lib/api";
import {
  fetchBlastRadius,
  fetchCallFlow,
  fetchModuleGraph,
  triggerIndexCode,
  type BlastRadius,
  type CallFlow,
  type ModuleGraph,
} from "@/lib/graph";
import { ModuleFlow } from "./graph/ModuleFlow";
import styles from "./GraphPanel.module.css";

type Tab = "modules" | "blast" | "callflow";

const TABS: { id: Tab; label: string }[] = [
  { id: "modules", label: "Module map" },
  { id: "blast", label: "Blast radius" },
  { id: "callflow", label: "Call flow" },
];

async function waitForModuleGraph(repoId: string, attempts = 20, delayMs = 2000): Promise<ModuleGraph> {
  let last: ModuleGraph = { nodes: [], edges: [] };
  for (let i = 0; i < attempts; i++) {
    await new Promise((r) => setTimeout(r, delayMs));
    last = await fetchModuleGraph(repoId);
    if (last.nodes.length > 0) return last;
  }
  return last;
}

export function GraphPanel({ repo }: { repo: Repository }) {
  const [tab, setTab] = useState<Tab>("modules");
  const [graph, setGraph] = useState<ModuleGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [indexMsg, setIndexMsg] = useState<string | null>(null);

  const [symbol, setSymbol] = useState("");
  const [blast, setBlast] = useState<BlastRadius | null>(null);
  const [flow, setFlow] = useState<CallFlow | null>(null);
  const [querying, setQuerying] = useState(false);

  const loadModules = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setGraph(await fetchModuleGraph(repo.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load graph");
    } finally {
      setLoading(false);
    }
  }, [repo.id]);

  useEffect(() => {
    void loadModules();
  }, [loadModules]);

  async function onIndex() {
    setIndexing(true);
    setIndexMsg(null);
    setError(null);
    try {
      const out = await triggerIndexCode(repo.id);
      setIndexMsg(
        `${out.message}${out.task_id ? ` (${out.task_id.slice(0, 8)}…)` : ""} — waiting for the worker…`,
      );
      setLoading(true);
      const g = await waitForModuleGraph(repo.id);
      setGraph(g);
      setIndexMsg(
        g.nodes.length === 0
          ? "Index finished but no modules yet. Ensure the worker is running, then retry."
          : `Indexed — ${g.nodes.length} modules, ${g.edges.length} links.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Index failed");
    } finally {
      setIndexing(false);
      setLoading(false);
    }
  }

  async function runQuery() {
    const s = symbol.trim();
    if (!s) return;
    setQuerying(true);
    setError(null);
    try {
      if (tab === "blast") setBlast(await fetchBlastRadius(repo.id, s));
      else if (tab === "callflow") setFlow(await fetchCallFlow(repo.id, s));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Query failed");
    } finally {
      setQuerying(false);
    }
  }

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Structure &amp; call graph</h2>
          <p className={styles.hint}>
            Module dependencies, blast radius, and call flows from the indexed call graph.
            Approximate links are dashed and labeled with confidence.
          </p>
        </div>
        <div className={styles.headActions}>
          <button
            type="button"
            className={styles.ghost}
            onClick={() => void loadModules()}
            disabled={loading || indexing}
          >
            Refresh
          </button>
          <button
            type="button"
            className={styles.primary}
            onClick={() => void onIndex()}
            disabled={indexing}
          >
            {indexing ? "Building…" : "Build structure"}
          </button>
        </div>
      </header>

      <div className={styles.tabs} role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={styles.tab}
            data-active={tab === t.id}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {indexMsg ? <div className={styles.banner}>{indexMsg}</div> : null}
      {error ? <div className={styles.error}>{error}</div> : null}

      {tab === "modules" ? (
        loading || indexing ? (
          <div className={styles.loading}>
            <span className={styles.spinner} />
            {indexing ? "Building symbols & call graph…" : "Loading module map…"}
          </div>
        ) : !graph || graph.nodes.length === 0 ? (
          <div className={styles.empty}>
            <div className={styles.emptyIcon}>◈</div>
            <p className={styles.emptyTitle}>No call graph yet</p>
            <p className={styles.emptyBody}>
              Click <strong>Build structure</strong> to parse the code and assemble the module map
              (the background worker must be running).
            </p>
          </div>
        ) : (
          <ModuleFlow graph={graph} />
        )
      ) : null}

      {tab === "blast" || tab === "callflow" ? (
        <div className={styles.query}>
          <div className={styles.queryRow}>
            <div className={styles.inputWrap}>
              <span className={styles.inputIcon}>ƒ</span>
              <input
                className={styles.input}
                placeholder="Symbol name (e.g. getCollections, Home)"
                value={symbol}
                onChange={(e) => setSymbol(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && void runQuery()}
              />
            </div>
            <button
              type="button"
              className={styles.primary}
              onClick={() => void runQuery()}
              disabled={querying}
            >
              {querying ? "…" : tab === "blast" ? "Analyze impact" : "Trace"}
            </button>
          </div>

          {tab === "blast" && blast ? (
            <div className={styles.result}>
              {blast.note ? <p className={styles.muted}>{blast.note}</p> : null}
              {blast.target ? (
                <div className={styles.targetLine}>
                  <code className={styles.codeAccent}>{blast.target.name}</code>
                  <span className={styles.sub}>{blast.target.path}</span>
                  <span className={styles.totalPill}>{blast.total} affected</span>
                </div>
              ) : null}
              {Object.entries(blast.by_category).map(([cat, items]) => (
                <div key={cat} className={styles.catBlock}>
                  <div className={styles.catLabel}>
                    {cat} <span className={styles.muted}>· {items.length}</span>
                  </div>
                  <ul className={styles.impactList}>
                    {items.map((it, i) => (
                      <li key={`${it.path}-${it.name}-${i}`} className={styles.impactRow}>
                        <code className={styles.codeAccent}>{it.name}</code>
                        <span className={styles.sub}>{it.path}</span>
                        <span className={styles.depth}>depth {it.depth}</span>
                        <ConfBadge value={it.confidence} />
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          ) : null}

          {tab === "callflow" && flow ? (
            <div className={styles.result}>
              {flow.note ? <p className={styles.muted}>{flow.note}</p> : null}
              {flow.steps.length > 0 ? (
                <ol className={styles.flowList}>
                  {flow.steps.map((s, i) => (
                    <li key={i} className={styles.flowStep}>
                      <span className={styles.stepNum}>{i + 1}</span>
                      <div className={styles.stepBody}>
                        <code className={styles.codeAccent}>{s.src}</code>
                        <span className={styles.arrow}>→</span>
                        <code className={styles.codeAccent}>{s.dst}</code>
                        {s.kind !== "calls" ? <span className={styles.kind}>{s.kind}</span> : null}
                        <ConfBadge value={s.confidence} />
                      </div>
                    </li>
                  ))}
                </ol>
              ) : null}
              {flow.mermaid ? (
                <details className={styles.mermaidWrap}>
                  <summary>Mermaid source</summary>
                  <pre className={styles.mermaid}>{flow.mermaid}</pre>
                </details>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function ConfBadge({ value }: { value: number }) {
  const high = value >= 0.7;
  return (
    <span className={high ? styles.confHigh : styles.confLow}>{Math.round(value * 100)}%</span>
  );
}
