"use client";

import { useCallback, useEffect, useState } from "react";
import type { Repository } from "@/lib/api";
import {
  architectureToModuleGraph,
  fetchArchitecture,
  fetchBrief,
  triggerIndexUnderstanding,
  type Architecture,
  type Brief,
} from "@/lib/understanding";
import { ModuleFlow } from "./graph/ModuleFlow";
import styles from "./OverviewPanel.module.css";

export function OverviewPanel({
  repo,
  onAsk,
  onOpenMode,
}: {
  repo: Repository;
  onAsk: (question: string) => void;
  onOpenMode: (mode: "architecture" | "flows" | "apis", opts?: { component?: string }) => void;
}) {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [arch, setArch] = useState<Architecture | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [b, a] = await Promise.all([fetchBrief(repo.id), fetchArchitecture(repo.id)]);
      setBrief(b);
      setArch(a);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load overview");
    } finally {
      setLoading(false);
    }
  }, [repo.id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onBuild() {
    setBuilding(true);
    setError(null);
    try {
      await triggerIndexUnderstanding(repo.id);
      for (let i = 0; i < 15; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const b = await fetchBrief(repo.id);
        setBrief(b);
        if (b.source === "cached") break;
      }
      setArch(await fetchArchitecture(repo.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Build failed");
    } finally {
      setBuilding(false);
    }
  }

  if (loading) {
    return (
      <section className={styles.panel}>
        <div className={styles.loading}>
          <span className={styles.spinner} />
          Building a mental model of this repository…
        </div>
      </section>
    );
  }

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <p className={styles.kicker}>Here&apos;s what you should know</p>
          <h2 className={styles.title}>{brief?.summary || "Repository overview"}</h2>
          <p className={styles.hint}>
            {brief?.source === "cached"
              ? "Generated from parsed code, manifests, and the call graph."
              : "Live sketch from the index. Click “Explain this repo” for the full brief."}
          </p>
        </div>
        <button type="button" className={styles.primary} onClick={() => void onBuild()} disabled={building}>
          {building ? "Explaining…" : "Explain this repo"}
        </button>
      </header>

      {error ? <div className={styles.error}>{error}</div> : null}

      <div className={styles.stats}>
        <Stat label="Files" value={brief?.file_count ?? 0} />
        <Stat label="Lines" value={brief?.loc ?? 0} />
        <Stat label="APIs" value={brief?.endpoint_count ?? 0} />
        <Stat label="Languages" value={Object.keys(brief?.languages || {}).join(" · ") || "—"} />
      </div>

      {brief && brief.frameworks.length > 0 ? (
        <div className={styles.chips}>
          {brief.frameworks.map((f) => (
            <span key={f} className={styles.chip}>
              {f}
            </span>
          ))}
          {(brief.externals || []).map((e, i) =>
            e.name ? (
              <span key={`${e.name}-${i}`} className={styles.chipMuted}>
                {e.name}
              </span>
            ) : null,
          )}
        </div>
      ) : null}

      <div className={styles.grid}>
        <div className={styles.card}>
          <h3>Domains</h3>
          {brief && brief.domains.length > 0 ? (
            <ul className={styles.list}>
              {brief.domains.map((d) => (
                <li key={d.name}>
                  <strong>{d.name}</strong>
                  <span>{d.folders.join(", ")}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>Index the repo to infer domains from folders.</p>
          )}
        </div>
        <div className={styles.card}>
          <h3>Start here</h3>
          {brief && brief.entry_points.length > 0 ? (
            <ul className={styles.list}>
              {brief.entry_points.slice(0, 8).map((e, i) => (
                <li key={`${e.path}-${i}`}>
                  <strong>{e.name || e.path}</strong>
                  <span>
                    {e.kind}
                    {e.path ? ` · ${e.path}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>Entry points appear after “Explain this repo”.</p>
          )}
        </div>
        <div className={styles.card}>
          <h3>Important files</h3>
          {brief && brief.hotspots.length > 0 ? (
            <ul className={styles.list}>
              {brief.hotspots.map((h) => (
                <li key={h.path}>
                  <button
                    type="button"
                    className={styles.hotspotBtn}
                    onClick={() =>
                      onOpenMode("architecture", {
                        component: h.layer && h.domain ? `${h.layer}:${h.domain}` : undefined,
                      })
                    }
                  >
                    <strong>{h.path}</strong>
                    <span>
                      {h.domain || h.layer} · {h.symbol_count} symbols · see impact
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.muted}>Hotspots show up once symbols are indexed.</p>
          )}
        </div>
      </div>

      <div className={styles.mapBlock}>
        <div className={styles.mapHead}>
          <h3>Architecture</h3>
          <button type="button" className={styles.linkish} onClick={() => onOpenMode("architecture")}>
            Open full map
          </button>
        </div>
        {arch && arch.nodes.length > 0 ? (
          <div className={styles.mapCanvas}>
            <ModuleFlow
              compact
              graph={architectureToModuleGraph(arch)}
              onSelect={(id) => {
                if (id) onOpenMode("architecture", { component: id });
              }}
            />
          </div>
        ) : (
          <p className={styles.muted}>
            The architecture map fills in after code indexing (Build structure / wait for the worker).
          </p>
        )}
      </div>

      <div className={styles.askRow}>
        <p className={styles.askLabel}>What would you like to understand?</p>
        <div className={styles.questions}>
          {(brief?.suggested_questions?.length
            ? brief.suggested_questions
            : [
                "What are the main components?",
                "Where does the application start?",
                "How does a request get handled?",
              ]
          ).map((q) => (
            <button key={q} type="button" className={styles.qchip} onClick={() => onAsk(q)}>
              {q}
            </button>
          ))}
          <button type="button" className={styles.qchip} onClick={() => onOpenMode("apis")}>
            Browse APIs
          </button>
          <button type="button" className={styles.qchip} onClick={() => onOpenMode("flows")}>
            Browse flows
          </button>
        </div>
      </div>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statVal}>{value}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  );
}
