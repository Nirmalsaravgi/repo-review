"use client";

import { useCallback, useEffect, useState } from "react";
import type { Repository } from "@/lib/api";
import { fetchBlastRadius, type BlastRadius } from "@/lib/graph";
import {
  architectureToModuleGraph,
  fetchArchitecture,
  fetchComponentImpact,
  fetchComponentMembers,
  fetchDecomposition,
  githubBlob,
  type Architecture,
  type ArchNode,
  type ComponentImpact,
  type ComponentMembers,
  type Decomposition,
  type ModuleNode,
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
  const [decomposition, setDecomposition] = useState<Decomposition | null>(null);
  const [view, setView] = useState<"responsibilities" | "folders">("responsibilities");
  const [selectedModule, setSelectedModule] = useState<ModuleNode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<ArchNode | null>(null);
  const [impact, setImpact] = useState<ComponentImpact | null>(null);
  const [members, setMembers] = useState<ComponentMembers | null>(null);
  const [impactBusy, setImpactBusy] = useState(false);
  const [symbolBlast, setSymbolBlast] = useState<BlastRadius | null>(null);

  const load = useCallback(async () => {
    try {
      const [g, d] = await Promise.all([
        fetchArchitecture(repo.id),
        fetchDecomposition(repo.id).catch(() => null),
      ]);
      setArch(g);
      setDecomposition(d);
      const hasModules = (d?.modules || []).some((node) => node.kind !== "application" || node.children.length > 0);
      if (!hasModules) setView("folders");
      const focus = initialComponent
        ? g.nodes.find((n) => n.id === initialComponent || `${n.layer_name}:${n.domain}` === initialComponent)
        : null;
      if (focus) {
        setView("folders");
        void inspect(focus);
      }
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
            Responsibilities come from the dependency graph. The folder map is the old layer view —
            useful for navigation, not the architecture.
          </p>
        </div>
        <div className={styles.toggle}>
          <button
            type="button"
            data-active={view === "responsibilities"}
            onClick={() => setView("responsibilities")}
          >
            Responsibilities
          </button>
          <button type="button" data-active={view === "folders"} onClick={() => setView("folders")}>
            Folder map
          </button>
        </div>
      </header>
      {error ? <div className={styles.error}>{error}</div> : null}
      {view === "responsibilities" ? (
        <ResponsibilityView
          repo={repo}
          decomposition={decomposition}
          selected={selectedModule}
          onSelect={setSelectedModule}
          onAsk={onAsk}
        />
      ) : !arch || arch.nodes.length === 0 ? (
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

function ResponsibilityView({
  repo,
  decomposition,
  selected,
  onSelect,
  onAsk,
}: {
  repo: Repository;
  decomposition: Decomposition | null;
  selected: ModuleNode | null;
  onSelect: (node: ModuleNode) => void;
  onAsk: (question: string, context?: string) => void;
}) {
  const roots = decomposition?.modules || [];
  const features = decomposition?.features || [];
  if (roots.length === 0) {
    return (
      <p className={styles.muted}>
        No responsibility map yet. Open Overview and click Explain this repo after code indexing finishes.
      </p>
    );
  }
  const files = selected?.evidence.files || [];
  const context = selected
    ? `Module ${selected.name} (${selected.kind}). Files: ${files.slice(0, 8).join(", ") || "—"}. Cohesion ${selected.cohesion}, coupling ${selected.coupling}.`
    : undefined;
  return (
    <div className={styles.split}>
      <div>
        <ModuleTree nodes={roots} selectedId={selected?.id || null} depth={0} onSelect={onSelect} />
        {features.length > 0 ? (
          <div className={styles.featureBlock}>
            <p className={styles.sideHint}>Cross-folder features</p>
            <ul className={styles.plainList}>
              {features.map((feature) => (
                <li key={feature.id}>
                  <div className={styles.rowBtn}>
                    <strong>{feature.name}</strong>
                    {feature.summary ? <span>{feature.summary}</span> : null}
                    <span>
                      {(feature.evidence.modules || []).join(" · ") || "one module"} ·{" "}
                      {(feature.evidence.folders || []).slice(0, 3).join(", ") || "—"} ·{" "}
                      {Math.round(feature.confidence * 100)}% · {feature.evidence.resolution || "call graph"}
                    </span>
                    {feature.evidence.path && feature.evidence.path.length > 0 ? (
                      <span>{feature.evidence.path.join(" → ")}</span>
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          </div>
        ) : (
          <p className={styles.muted}>No cross-folder feature detected from the indexed call graph yet.</p>
        )}
      </div>
      <aside className={styles.side}>
        {selected ? (
          <div className={styles.detail}>
            <h3>{selected.name}</h3>
            <p className={styles.muted}>
              {selected.kind} · cohesion {selected.cohesion.toFixed(2)} · coupling {selected.coupling.toFixed(2)}
            </p>
            <p className={styles.muted}>{selected.evidence.reason || selected.evidence.method || "Grouped from dependencies."}</p>
            {selected.summary ? <p>{selected.summary}</p> : null}
            {files.length > 0 ? (
              <>
                <p className={styles.cat}>Files · {files.length}</p>
                <ul className={styles.plainList}>
                  {files.slice(0, 16).map((path) => (
                    <li key={path}>
                      <a className={styles.fileLink} href={githubBlob(repo, path)} target="_blank" rel="noreferrer">
                        <code>{path}</code>
                      </a>
                    </li>
                  ))}
                </ul>
              </>
            ) : selected.evidence.child_modules ? (
              <p className={styles.muted}>Contains {selected.evidence.child_modules.join(", ")}</p>
            ) : null}
            <button
              type="button"
              className={styles.primary}
              onClick={() =>
                onAsk(
                  `What is the ${selected.name} module responsible for, and which features cross its boundary?`,
                  context,
                )
              }
            >
              Explain in chat
            </button>
          </div>
        ) : (
          <p className={styles.muted}>Select a module to see why those files were grouped together.</p>
        )}
      </aside>
    </div>
  );
}

function ModuleTree({
  nodes,
  selectedId,
  depth,
  onSelect,
}: {
  nodes: ModuleNode[];
  selectedId: string | null;
  depth: number;
  onSelect: (node: ModuleNode) => void;
}) {
  if (nodes.length === 0) return null;
  return (
    <ul className={styles.tree} style={{ marginLeft: depth ? "0.85rem" : 0 }}>
      {nodes.map((node) => (
        <li key={node.id}>
          <button
            type="button"
            className={styles.rowBtn}
            data-active={selectedId === node.id}
            onClick={() => onSelect(node)}
          >
            <strong>{node.name}</strong>
            <span>
              {node.kind}
              {node.file_count ? ` · ${node.file_count} files` : ""}
              {node.kind === "module" ? ` · cohesion ${node.cohesion.toFixed(2)}` : ""}
            </span>
          </button>
          <ModuleTree nodes={node.children} selectedId={selectedId} depth={depth + 1} onSelect={onSelect} />
        </li>
      ))}
    </ul>
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
