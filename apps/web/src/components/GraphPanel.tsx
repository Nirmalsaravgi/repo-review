"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
import styles from "./GraphPanel.module.css";

type Tab = "modules" | "blast" | "callflow";

const NODE_W = 150;
const NODE_H = 34;

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
      // Full pipeline: parse symbols → embed → edges. Graph-only indexing is not enough
      // when symbols were never built (or a prior index_code failed).
      const out = await triggerIndexCode(repo.id);
      setIndexMsg(
        `${out.message}${out.task_id ? ` (${out.task_id.slice(0, 8)}…)` : ""} — waiting for Celery…`,
      );
      setLoading(true);
      const g = await waitForModuleGraph(repo.id);
      setGraph(g);
      if (g.nodes.length === 0) {
        setIndexMsg(
          "Index finished but no modules yet. Ensure the Celery worker is running, then retry.",
        );
      } else {
        setIndexMsg(`Indexed — ${g.nodes.length} modules, ${g.edges.length} links.`);
      }
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
      if (tab === "blast") {
        setBlast(await fetchBlastRadius(repo.id, s));
      } else if (tab === "callflow") {
        setFlow(await fetchCallFlow(repo.id, s));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Query failed");
    } finally {
      setQuerying(false);
    }
  }

  const viewBox = useMemo(() => {
    if (!graph || graph.nodes.length === 0) return "0 0 400 200";
    const maxX = Math.max(...graph.nodes.map((n) => n.x)) + NODE_W + 20;
    const maxY = Math.max(...graph.nodes.map((n) => n.y)) + NODE_H + 20;
    return `-10 -10 ${maxX} ${maxY}`;
  }, [graph]);

  const nodeById = useMemo(() => {
    const m = new Map<string, { x: number; y: number }>();
    for (const n of graph?.nodes || []) m.set(n.id, { x: n.x, y: n.y });
    return m;
  }, [graph]);

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Structure &amp; call graph</h2>
          <p className={styles.hint}>
            Module dependencies, blast radius, and call flows from the indexed call graph.
            Approximate (name-match / event) links are shown dashed &amp; labeled with confidence.
            Selecting a repo starts indexing automatically; use the button to rebuild if empty.
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
          <button type="button" className={styles.primary} onClick={() => void onIndex()} disabled={indexing}>
            {indexing ? "Indexing…" : "Build structure"}
          </button>
        </div>
      </header>

      <div className={styles.tabs} role="tablist">
        {(
          [
            ["modules", "Module map"],
            ["blast", "Blast radius"],
            ["callflow", "Call flow"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? styles.tabActive : styles.tab}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      {indexMsg ? <div className={styles.banner}>{indexMsg}</div> : null}
      {error ? <div className={styles.error}>{error}</div> : null}

      {tab === "modules" ? (
        loading || indexing ? (
          <p className={styles.muted}>
            {indexing ? "Building symbols & call graph (Celery)…" : "Loading…"}
          </p>
        ) : !graph || graph.nodes.length === 0 ? (
          <div className={styles.empty}>
            <p>No call graph yet.</p>
            <p className={styles.muted}>
              Click <strong>Build structure</strong> (Celery worker must be running). This parses
              the code, then builds the module map — same job that normally starts after you
              select a repo.
            </p>
          </div>
        ) : (
          <div className={styles.canvas}>
            <svg viewBox={viewBox} className={styles.svg} role="img" aria-label="Module dependency graph">
              {graph.edges.map((e) => {
                const a = nodeById.get(e.src);
                const b = nodeById.get(e.dst);
                if (!a || !b) return null;
                return (
                  <line
                    key={`${e.src}->${e.dst}`}
                    x1={a.x + NODE_W / 2}
                    y1={a.y + NODE_H}
                    x2={b.x + NODE_W / 2}
                    y2={b.y}
                    className={e.confidence >= 0.7 ? styles.edge : styles.edgeWeak}
                    strokeWidth={Math.min(1 + e.weight / 3, 4)}
                  />
                );
              })}
              {graph.nodes.map((n) => (
                <g key={n.id} transform={`translate(${n.x}, ${n.y})`}>
                  <rect width={NODE_W} height={NODE_H} rx={6} className={styles.node} />
                  <text x={8} y={15} className={styles.nodeLabel}>
                    {n.label.length > 20 ? `…${n.label.slice(-19)}` : n.label}
                  </text>
                  <text x={8} y={28} className={styles.nodeSub}>
                    {n.symbol_count} symbols
                  </text>
                </g>
              ))}
            </svg>
          </div>
        )
      ) : null}

      {tab === "blast" || tab === "callflow" ? (
        <div className={styles.query}>
          <div className={styles.queryRow}>
            <input
              className={styles.input}
              placeholder="Symbol name (e.g. getCollections, Home)"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && void runQuery()}
            />
            <button type="button" className={styles.primary} onClick={() => void runQuery()} disabled={querying}>
              {querying ? "…" : tab === "blast" ? "Analyze impact" : "Trace"}
            </button>
          </div>

          {tab === "blast" && blast ? (
            <div className={styles.result}>
              {blast.note ? <p className={styles.muted}>{blast.note}</p> : null}
              {blast.target ? (
                <p className={styles.targetLine}>
                  <code>{blast.target.name}</code> · {blast.target.path} · <b>{blast.total}</b> affected
                </p>
              ) : null}
              {Object.entries(blast.by_category).map(([cat, items]) => (
                <div key={cat} className={styles.catBlock}>
                  <div className={styles.catLabel}>
                    {cat} <span className={styles.muted}>({items.length})</span>
                  </div>
                  <ul className={styles.impactList}>
                    {items.map((it, i) => (
                      <li key={`${it.path}-${it.name}-${i}`}>
                        <code>{it.name}</code>
                        <span className={styles.sub}>{it.path}</span>
                        <span className={styles.depth}>d{it.depth}</span>
                        <span className={it.confidence >= 0.7 ? styles.confHigh : styles.confLow}>
                          {Math.round(it.confidence * 100)}%
                        </span>
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
                    <li key={i}>
                      <code>{s.src}</code> <span className={styles.arrow}>→</span> <code>{s.dst}</code>
                      {s.kind !== "calls" ? <span className={styles.kind}>{s.kind}</span> : null}
                      <span className={s.confidence >= 0.7 ? styles.confHigh : styles.confLow}>
                        {Math.round(s.confidence * 100)}%
                      </span>
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
