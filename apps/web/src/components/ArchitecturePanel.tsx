"use client";

import { useCallback, useEffect, useState } from "react";
import type { Repository } from "@/lib/api";
import { fetchBlastRadius, type BlastRadius } from "@/lib/graph";
import {
  architectureToModuleGraph,
  fetchArchitecture,
  fetchComponentImpact,
  fetchComponentMembers,
  githubBlob,
  type Architecture,
  type ArchNode,
  type ComponentImpact,
  type ComponentMembers,
} from "@/lib/understanding";
import { ModuleFlow } from "./graph/ModuleFlow";
import styles from "./ExplorePanel.module.css";

export function ArchitecturePanel({
  repo,
  onAsk,
  initialComponent,
}: {
  repo: Repository;
  onAsk: (question: string, context?: string) => void;
  initialComponent?: string | null;
}) {
  const [arch, setArch] = useState<Architecture | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ArchNode | null>(null);
  const [impact, setImpact] = useState<ComponentImpact | null>(null);
  const [members, setMembers] = useState<ComponentMembers | null>(null);
  const [impactBusy, setImpactBusy] = useState(false);
  const [symbolBlast, setSymbolBlast] = useState<BlastRadius | null>(null);

  const load = useCallback(async () => {
    try {
      const g = await fetchArchitecture(repo.id);
      setArch(g);
      const focus = initialComponent
        ? g.nodes.find((n) => n.id === initialComponent || `${n.layer_name}:${n.domain}` === initialComponent)
        : null;
      if (focus) void inspect(focus);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load architecture");
    }
  }, [repo.id, initialComponent]);

  useEffect(() => {
    void load();
  }, [load]);

  async function inspect(node: ArchNode) {
    setSelected(node);
    setImpact(null);
    setMembers(null);
    setSymbolBlast(null);
    setImpactBusy(true);
    try {
      const [imp, mem] = await Promise.all([
        fetchComponentImpact(repo.id, node.id),
        fetchComponentMembers(repo.id, node.id),
      ]);
      setImpact(imp);
      setMembers(mem);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Impact lookup failed");
    } finally {
      setImpactBusy(false);
    }
  }

  async function inspectSymbol(name: string) {
    setSymbolBlast(null);
    try {
      setSymbolBlast(await fetchBlastRadius(repo.id, name));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Symbol impact failed");
    }
  }

  const context = selected
    ? `Architecture component ${selected.label} (${selected.id}). Folders: ${selected.folders.join(", ") || "—"}`
    : undefined;

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Architecture</h2>
          <p className={styles.hint}>
            Click a box to drill into files and symbols, then see what else depends on it. Dashed /
            low-confidence links are likely, not certain.
          </p>
        </div>
      </header>
      {error ? <div className={styles.error}>{error}</div> : null}
      {!arch || arch.nodes.length === 0 ? (
        <p className={styles.muted}>No architecture yet. Wait for code indexing, or open Overview and click Explain.</p>
      ) : (
        <div className={styles.split}>
          <ModuleFlow
            graph={architectureToModuleGraph(arch)}
            onSelect={(id) => {
              const node = arch.nodes.find((n) => n.id === id);
              if (node) void inspect(node);
              else {
                setSelected(null);
                setImpact(null);
                setMembers(null);
              }
            }}
          />
          <aside className={styles.side}>
            <p className={styles.sideHint}>Components</p>
            <ul className={styles.plainList}>
              {arch.nodes.map((n) => (
                <li key={n.id}>
                  <button
                    type="button"
                    className={styles.rowBtn}
                    data-active={selected?.id === n.id}
                    onClick={() => void inspect(n)}
                  >
                    <strong>{n.label}</strong>
                    <span>
                      {n.layer_name} · {n.file_count} files · {n.symbol_count} symbols
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            {selected ? (
              <div className={styles.detail}>
                <h3>{selected.label}</h3>
                <p className={styles.muted}>Folders: {selected.folders.join(", ") || "—"}</p>
                {impactBusy ? <p className={styles.muted}>Loading files and impact…</p> : null}
                {members && members.files.length > 0 ? (
                  <>
                    <p className={styles.cat}>Files · {members.files.length}</p>
                    <ul className={styles.plainList}>
                      {members.files.slice(0, 12).map((f) => (
                        <li key={f.path}>
                          <a
                            className={styles.fileLink}
                            href={githubBlob(repo, f.path, f.start_line)}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <code>{f.path}</code>
                            <span>{f.symbol_count} symbols</span>
                          </a>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
                {members && members.symbols.length > 0 ? (
                  <>
                    <p className={styles.cat}>Symbols · click for impact</p>
                    <ul className={styles.plainList}>
                      {members.symbols.slice(0, 16).map((s) => (
                        <li key={`${s.path}-${s.name}-${s.start_line}`}>
                          <button
                            type="button"
                            className={styles.rowBtn}
                            onClick={() => void inspectSymbol(s.name)}
                          >
                            <strong>{s.name}</strong>
                            <span>
                              {s.kind} · {s.path}:{s.start_line}
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
                {symbolBlast ? (
                  <div className={styles.impact}>
                    <span className={styles.risk} data-risk={symbolBlast.total > 8 ? "high" : "low"}>
                      {symbolBlast.symbol}
                    </span>
                    <p>
                      Changing {symbolBlast.symbol} may affect {symbolBlast.total} symbols.
                    </p>
                  </div>
                ) : null}
                {impact ? <ImpactBody repo={repo} impact={impact} /> : null}
                <button
                  type="button"
                  className={styles.primary}
                  onClick={() =>
                    onAsk(
                      `If I change the ${selected.label} ${selected.layer_name} code, what else might break?`,
                      context,
                    )
                  }
                >
                  Explain in chat
                </button>
              </div>
            ) : (
              <p className={styles.muted}>Click a box on the map to see files, symbols, and blast radius.</p>
            )}
          </aside>
        </div>
      )}
    </section>
  );
}

function ImpactBody({ repo, impact }: { repo: Repository; impact: ComponentImpact }) {
  return (
    <div className={styles.impact}>
      <span className={styles.risk} data-risk={impact.risk}>
        {impact.risk === "none" ? "Low coupling" : `${impact.risk} impact`}
      </span>
      <p>{impact.summary}</p>
      {impact.endpoints.length > 0 ? (
        <p className={styles.muted}>
          APIs in this box: {impact.endpoints.map((e) => `${e.method} ${e.path}`).join(" · ")}
        </p>
      ) : null}
      {Object.entries(impact.by_category).map(([cat, items]) => (
        <div key={cat}>
          <div className={styles.cat}>
            {cat} · {items.length}
          </div>
          <ul className={styles.plainList}>
            {items.slice(0, 8).map((it, i) => (
              <li key={`${it.path}-${it.name}-${i}`}>
                <a className={styles.fileLink} href={githubBlob(repo, it.path)} target="_blank" rel="noreferrer">
                  <code>{it.name}</code>
                  <span>{it.path}</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
