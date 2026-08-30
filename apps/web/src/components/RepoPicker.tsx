"use client";

import { useMemo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { Repository } from "@/lib/api";
import { isFlowQuestion, queryFlow } from "@/lib/understanding";
import { ArchitecturePanel } from "./ArchitecturePanel";
import { ApisPanel } from "./ApisPanel";
import { ChatPanel } from "./ChatPanel";
import { FlowsPanel } from "./FlowsPanel";
import { GraphPanel } from "./GraphPanel";
import { HistoryPanel } from "./HistoryPanel";
import { OverviewPanel } from "./OverviewPanel";
import styles from "./RepoPicker.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001";

type View = "overview" | "architecture" | "flows" | "apis" | "chat" | "more";

const VIEWS: { id: View; label: string; icon: string }[] = [
  { id: "overview", label: "Overview", icon: "◎" },
  { id: "architecture", label: "Architecture", icon: "◈" },
  { id: "flows", label: "Flows", icon: "↝" },
  { id: "apis", label: "APIs", icon: "⎘" },
  { id: "chat", label: "Ask", icon: "✦" },
  { id: "more", label: "More", icon: "⋯" },
];

export function RepoPicker({
  initialRepos,
  installUrl,
}: {
  initialRepos: Repository[];
  installUrl: string | null;
}) {
  const [repos, setRepos] = useState(initialRepos);
  const [filter, setFilter] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("overview");
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [chatContext, setChatContext] = useState<string | null>(null);
  const [focusComponent, setFocusComponent] = useState<string | null>(null);
  const [focusFlowId, setFocusFlowId] = useState<string | null>(null);
  const [focusFlowTitle, setFocusFlowTitle] = useState<string | null>(null);
  const [flowQuerying, setFlowQuerying] = useState(false);

  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return repos;
    return repos.filter((r) => r.full_name.toLowerCase().includes(q));
  }, [repos, filter]);

  const activeRepo = useMemo(
    () => repos.find((r) => r.selected && r.index_status === "ready") || null,
    [repos],
  );

  async function syncRepos() {
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/repos/sync`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(await res.text());
      const data = (await res.json()) as Repository[];
      setRepos(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sync failed");
    }
  }

  async function selectRepo(githubRepoId: number) {
    setBusyId(githubRepoId);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/repos/select`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ github_repo_id: githubRepoId }),
      });
      if (!res.ok) throw new Error(await res.text());
      const updated = (await res.json()) as Repository;
      setRepos((prev) =>
        prev.map((r) =>
          r.github_repo_id === updated.github_repo_id ? updated : { ...r, selected: false },
        ),
      );
      pollStatus(updated.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Select failed");
    } finally {
      setBusyId(null);
    }
  }

  async function handleAsk(question: string, opts?: { context?: string }) {
    if (opts?.context) setChatContext(opts.context);
    const repo = repos.find((r) => r.selected && r.index_status === "ready");
    if (repo && isFlowQuestion(question)) {
      setFlowQuerying(true);
      try {
        const flow = await queryFlow(repo.id, question);
        if (flow.matched && flow.id) {
          setFocusFlowId(flow.id);
          setFocusFlowTitle(null);
          setView("flows");
          return;
        }
        if (flow.retrieved_files?.length) {
          setChatContext(
            `No seeded flow matched. Retrieved files: ${flow.retrieved_files.slice(0, 6).join(", ")}`,
          );
        }
      } catch {
        /* fall through to chat */
      } finally {
        setFlowQuerying(false);
      }
    }
    setPendingQuestion(question);
    setView("chat");
  }

  async function pollStatus(repoId: string) {
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const res = await fetch(`${API_BASE}/repos/${repoId}`, { credentials: "include" });
      if (!res.ok) break;
      const row = (await res.json()) as Repository;
      setRepos((prev) => prev.map((r) => (r.id === row.id ? row : r)));
      if (row.index_status === "ready" || row.index_status === "error") break;
    }
  }

  return (
    <section className={styles.wrap}>
      <div className={styles.toolbar}>
        <div>
          <h1 className={styles.title}>Repositories</h1>
          <p className={styles.copy}>
            Select a repo to clone it. We build a brief, architecture map, and chat index in the
            background.
          </p>
        </div>
        <div className={styles.actions}>
          <button type="button" className={styles.ghost} onClick={syncRepos}>
            <span className={styles.ghostIcon}>↻</span> Sync from GitHub
          </button>
          {installUrl ? (
            <a className={styles.ghost} href={installUrl}>
              Manage install
            </a>
          ) : null}
        </div>
      </div>

      <div className={styles.searchWrap}>
        <span className={styles.searchIcon}>⌕</span>
        <input
          className={styles.search}
          placeholder="Filter repositories…"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
      </div>

      {error ? <div className={styles.error}>{error}</div> : null}

      {filtered.length === 0 ? (
        <div className={styles.empty}>
          No repositories yet. Install the GitHub App on an account or org, then sync.
        </div>
      ) : (
        <div className={styles.grid}>
          {filtered.map((repo) => {
            const active = repo.selected;
            const cloning = repo.index_status === "cloning";
            return (
              <motion.button
                key={repo.id}
                type="button"
                layout
                whileHover={{ y: -2 }}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
                className={active ? styles.cardActive : styles.card}
                disabled={busyId === repo.github_repo_id || cloning}
                onClick={() => selectRepo(repo.github_repo_id)}
              >
                <div className={styles.cardTop}>
                  <span className={styles.repoName}>{repo.full_name}</span>
                  <span className={styles.privacy}>{repo.private ? "private" : "public"}</span>
                </div>
                <div className={styles.cardMeta}>
                  <span className={styles.statusDot} data-status={repo.index_status} />
                  <span className={styles.statusText} data-status={repo.index_status}>
                    {cloning
                      ? "Cloning…"
                      : repo.index_status === "ready"
                        ? "Ready"
                        : repo.index_status}
                    {repo.is_shallow && repo.index_status === "ready" ? " · shallow" : ""}
                    {repo.index_status === "ready" && repo.index_fresh === false ? " · syncing" : ""}
                  </span>
                  {repo.last_indexed_sha ? (
                    <span className={styles.sha}>{repo.last_indexed_sha.slice(0, 7)}</span>
                  ) : null}
                </div>
                {repo.index_error ? <span className={styles.errDetail}>{repo.index_error}</span> : null}
                <span className={styles.cardCta}>
                  {cloning ? "Cloning…" : active ? "Selected — re-clone" : "Select repo →"}
                </span>
              </motion.button>
            );
          })}
        </div>
      )}

      {activeRepo ? (
        <div className={styles.workspace}>
          <div className={styles.workspaceHead}>
            <div className={styles.repoHeading}>
              <span className={styles.repoHeadingName}>{activeRepo.full_name}</span>
              <span className={styles.branch}>{activeRepo.default_branch}</span>
            </div>
            {flowQuerying ? <span className={styles.branch}>Tracing a flow…</span> : null}
            <div className={styles.segmented}>
              {VIEWS.map((v) => (
                <button
                  key={v.id}
                  type="button"
                  className={styles.segment}
                  data-active={view === v.id}
                  onClick={() => {
                    setFocusComponent(null);
                    setFocusFlowId(null);
                    setFocusFlowTitle(null);
                    setView(v.id);
                  }}
                >
                  {view === v.id ? (
                    <motion.span layoutId="segbg" className={styles.segbg} transition={{ type: "spring", stiffness: 500, damping: 40 }} />
                  ) : null}
                  <span className={styles.segIcon}>{v.icon}</span>
                  <span className={styles.segLabel}>{v.label}</span>
                </button>
              ))}
            </div>
          </div>

          <AnimatePresence mode="wait">
            <motion.div
              key={view}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18 }}
            >
              {view === "overview" ? (
                <OverviewPanel
                  repo={activeRepo}
                  onAsk={(q) => void handleAsk(q)}
                  onOpenMode={(mode, opts) => {
                    setFocusComponent(opts?.component ?? null);
                    setView(mode);
                  }}
                />
              ) : null}
              {view === "architecture" ? (
                <ArchitecturePanel
                  repo={activeRepo}
                  initialComponent={focusComponent}
                  onAsk={(q, ctx) => void handleAsk(q, { context: ctx })}
                />
              ) : null}
              {view === "flows" ? (
                <FlowsPanel
                  repo={activeRepo}
                  initialFlowId={focusFlowId}
                  initialTitle={focusFlowTitle}
                  onAsk={(q, ctx) => void handleAsk(q, { context: ctx })}
                />
              ) : null}
              {view === "apis" ? (
                <ApisPanel
                  repo={activeRepo}
                  onOpenFlow={(title) => {
                    setFocusFlowTitle(title);
                    setFocusFlowId(null);
                    setView("flows");
                  }}
                  onAsk={(q) => void handleAsk(q)}
                />
              ) : null}
              {view === "chat" ? (
                <ChatPanel
                  repo={activeRepo}
                  initialQuestion={pendingQuestion}
                  contextNote={chatContext}
                  onQuestionConsumed={() => setPendingQuestion(null)}
                  onContextConsumed={() => setChatContext(null)}
                />
              ) : null}
              {view === "more" ? (
                <div className={styles.moreStack}>
                  <HistoryPanel repo={activeRepo} />
                  <GraphPanel repo={activeRepo} />
                </div>
              ) : null}
            </motion.div>
          </AnimatePresence>
        </div>
      ) : (
        <p className={styles.footnote}>
          Select a repository and wait for it to reach “Ready” to start asking questions and view
          structure &amp; git intelligence.
        </p>
      )}
    </section>
  );
}
