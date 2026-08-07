"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { Repository } from "@/lib/api";
import {
  fetchBusFactor,
  fetchContributions,
  fetchOwnership,
  triggerIndexHistory,
  type BusFactorEntry,
  type ContributionEntry,
  type OwnershipEntry,
} from "@/lib/history";
import styles from "./HistoryPanel.module.css";

type Tab = "contributions" | "ownership" | "bus-factor";

export function HistoryPanel({ repo }: { repo: Repository }) {
  const [tab, setTab] = useState<Tab>("contributions");
  const [contributions, setContributions] = useState<ContributionEntry[]>([]);
  const [ownership, setOwnership] = useState<OwnershipEntry[]>([]);
  const [busFactor, setBusFactor] = useState<BusFactorEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [indexing, setIndexing] = useState(false);
  const [indexMsg, setIndexMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [c, o, b] = await Promise.all([
        fetchContributions(repo.id),
        fetchOwnership(repo.id),
        fetchBusFactor(repo.id),
      ]);
      setContributions(c);
      setOwnership(o);
      setBusFactor(b);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load history");
    } finally {
      setLoading(false);
    }
  }, [repo.id]);

  useEffect(() => {
    void load();
  }, [load]);

  const empty = !loading && contributions.length === 0 && ownership.length === 0;

  const topPrefixes = useMemo(() => {
    const byPrefix = new Map<string, OwnershipEntry[]>();
    for (const row of ownership) {
      const list = byPrefix.get(row.path_prefix) || [];
      list.push(row);
      byPrefix.set(row.path_prefix, list);
    }
    return [...byPrefix.entries()]
      .map(([prefix, rows]) => ({
        prefix,
        rows: rows.sort((a, b) => b.score - a.score).slice(0, 5),
        total: rows.reduce((s, r) => s + r.score, 0),
      }))
      .sort((a, b) => b.total - a.total)
      .slice(0, 12);
  }, [ownership]);

  async function onIndex() {
    setIndexing(true);
    setIndexMsg(null);
    setError(null);
    try {
      const out = await triggerIndexHistory(repo.id);
      setIndexMsg(`${out.message}${out.task_id ? ` (${out.task_id.slice(0, 8)}…)` : ""}`);
      // Give the worker a moment, then refresh
      setTimeout(() => void load(), 4000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Index failed");
    } finally {
      setIndexing(false);
    }
  }

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Git intelligence</h2>
          <p className={styles.hint}>
            Ownership, bus-factor, and contributors from indexed history
            {repo.is_shallow ? " · clone is still shallow until history finishes" : ""}.
          </p>
        </div>
        <div className={styles.headActions}>
          <button type="button" className={styles.ghost} onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
          <button type="button" className={styles.primary} onClick={() => void onIndex()} disabled={indexing}>
            {indexing ? "Enqueueing…" : "Index history"}
          </button>
        </div>
      </header>

      <div className={styles.tabs} role="tablist">
        {(
          [
            ["contributions", "Contributors"],
            ["ownership", "Ownership"],
            ["bus-factor", "Bus factor"],
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
      {loading ? <p className={styles.muted}>Loading…</p> : null}

      {!loading && empty ? (
        <div className={styles.empty}>
          <p>No history indexed yet.</p>
          <p className={styles.muted}>
            Chat still works on the shallow clone. Click <strong>Index history</strong> (Celery
            worker must be running) to deepen the clone and fill these panels.
          </p>
        </div>
      ) : null}

      {!loading && !empty && tab === "contributions" ? (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Author</th>
                <th>Commits</th>
                <th>Last seen</th>
              </tr>
            </thead>
            <tbody>
              {contributions.map((c) => (
                <tr key={c.author_id}>
                  <td>
                    <span className={styles.author}>{c.github_login || c.name || c.email}</span>
                    {c.github_login && c.email ? (
                      <span className={styles.sub}>{c.email}</span>
                    ) : null}
                  </td>
                  <td className={styles.num}>{c.commit_count}</td>
                  <td className={styles.muted}>{formatDate(c.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {!loading && !empty && tab === "ownership" ? (
        <div className={styles.ownership}>
          {topPrefixes.length === 0 ? (
            <p className={styles.muted}>No ownership rows yet.</p>
          ) : (
            topPrefixes.map(({ prefix, rows }) => (
              <div key={prefix} className={styles.prefixBlock}>
                <div className={styles.prefixLabel}>
                  <code>{prefix}</code>
                </div>
                <ul className={styles.bars}>
                  {rows.map((r) => {
                    const pct = Math.round((r.share ?? 0) * 100);
                    return (
                      <li key={`${r.path_prefix}-${r.author_id}`}>
                        <div className={styles.barMeta}>
                          <span>{r.author || r.github_login || r.email}</span>
                          <span className={styles.muted}>{pct}%</span>
                        </div>
                        <div className={styles.barTrack}>
                          <div className={styles.barFill} style={{ width: `${pct}%` }} />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </div>
            ))
          )}
        </div>
      ) : null}

      {!loading && !empty && tab === "bus-factor" ? (
        busFactor.length === 0 ? (
          <p className={styles.muted}>No bus-factor hotspots (no prefix above 70% ownership).</p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Path</th>
                  <th>Share</th>
                  <th>Status</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {busFactor.map((h) => (
                  <tr key={`${h.path_prefix}-${h.author_id}`}>
                    <td>
                      <code>{h.path_prefix}</code>
                    </td>
                    <td className={styles.num}>{Math.round(h.share * 100)}%</td>
                    <td>
                      <span
                        className={h.inactive ? styles.badgeDanger : styles.badgeWarn}
                      >
                        {h.inactive ? "concentrated · inactive" : "concentrated"}
                      </span>
                    </td>
                    <td className={styles.muted}>{formatDate(h.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </section>
  );
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso.slice(0, 10);
  }
}
