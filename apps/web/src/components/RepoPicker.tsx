"use client";

import { useMemo, useState } from "react";
import type { Repository } from "@/lib/api";
import { ChatPanel } from "./ChatPanel";
import { HistoryPanel } from "./HistoryPanel";
import styles from "./RepoPicker.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001";

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
          r.github_repo_id === updated.github_repo_id
            ? updated
            : { ...r, selected: false },
        ),
      );
      pollStatus(updated.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Select failed");
    } finally {
      setBusyId(null);
    }
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
          <h1 className={styles.title}>Select a repository</h1>
          <p className={styles.copy}>
            Selecting a repo starts a shallow clone for chat. History indexing (ownership,
            bus-factor) runs in the background via Celery after the clone is ready.
          </p>
        </div>
        <div className={styles.actions}>
          <button type="button" className={styles.ghost} onClick={syncRepos}>
            Sync from GitHub
          </button>
          {installUrl ? (
            <a className={styles.ghostLink} href={installUrl}>
              Manage App install
            </a>
          ) : null}
        </div>
      </div>

      <input
        className={styles.search}
        placeholder="Filter repositories…"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />

      {error ? <div className={styles.error}>{error}</div> : null}

      {filtered.length === 0 ? (
        <div className={styles.empty}>
          No repositories yet. Install the GitHub App on an account/org, then sync.
        </div>
      ) : (
        <ul className={styles.list}>
          {filtered.map((repo) => (
            <li key={repo.id} className={repo.selected ? styles.rowSelected : styles.row}>
              <div className={styles.meta}>
                <span className={styles.name}>{repo.full_name}</span>
                <span className={styles.badge}>{repo.private ? "private" : "public"}</span>
                <span className={styles.status} data-status={repo.index_status}>
                  {repo.index_status}
                  {repo.is_shallow && repo.index_status === "ready" ? " · shallow" : ""}
                  {repo.index_status === "ready" && repo.index_fresh === false
                    ? " · syncing"
                    : ""}
                </span>
                {repo.index_error ? (
                  <span className={styles.errDetail}>{repo.index_error}</span>
                ) : null}
                {repo.last_indexed_sha ? (
                  <span className={styles.sha}>
                    idx {repo.last_indexed_sha.slice(0, 12)}
                    {repo.head_sha && repo.head_sha !== repo.last_indexed_sha
                      ? ` · head ${repo.head_sha.slice(0, 12)}`
                      : ""}
                  </span>
                ) : null}
              </div>
              <button
                type="button"
                className={styles.select}
                disabled={busyId === repo.github_repo_id || repo.index_status === "cloning"}
                onClick={() => selectRepo(repo.github_repo_id)}
              >
                {repo.index_status === "cloning"
                  ? "Cloning…"
                  : repo.selected
                    ? "Re-clone"
                    : "Select"}
              </button>
            </li>
          ))}
        </ul>
      )}

      {activeRepo ? (
        <div className={styles.workspace}>
          <ChatPanel repo={activeRepo} />
          <HistoryPanel repo={activeRepo} />
        </div>
      ) : (
        <p className={styles.footnote}>
          Select a repository and wait for it to finish cloning (status “ready”) to start asking
          questions and view git intelligence.
        </p>
      )}
    </section>
  );
}
