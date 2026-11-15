"use client";

import { useCallback, useEffect, useState } from "react";
import type { Repository } from "@/lib/api";
import { Markdown } from "./chat/Markdown";
import {
  fetchWiki,
  fetchWikiPage,
  githubBlob,
  type WikiNavItem,
  type WikiPage,
} from "@/lib/understanding";
import styles from "./ExplorePanel.module.css";

export function WikiPanel({
  repo,
  initialSlug,
  onAsk,
  onOpenArchitecture,
}: {
  repo: Repository;
  initialSlug?: string | null;
  onAsk: (question: string, context?: string) => void;
  onOpenArchitecture: () => void;
}) {
  const [nav, setNav] = useState<WikiNavItem[]>([]);
  const [page, setPage] = useState<WikiPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const open = useCallback(
    async (slug: string) => {
      setError(null);
      try {
        setPage(await fetchWikiPage(repo.id, slug));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to open page");
      }
    },
    [repo.id],
  );

  const load = useCallback(async () => {
    try {
      const tree = await fetchWiki(repo.id);
      setNav(tree.pages);
      const slug = initialSlug && tree.pages.some((item) => item.slug === initialSlug) ? initialSlug : tree.pages[0]?.slug;
      if (slug) await open(slug);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Wiki is not published yet");
    }
  }, [repo.id, initialSlug, open]);

  useEffect(() => {
    void load();
  }, [load]);

  const context = page
    ? `Wiki page ${page.title} (${page.slug}). ${page.body.slice(0, 500)}`
    : undefined;

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Read</h2>
          <p className={styles.hint}>
            Pages are the stored explanations. Diagrams are drawn from the module graph, and source links go to the files those pages cite.
          </p>
        </div>
      </header>
      {error ? <div className={styles.error}>{error}</div> : null}
      {nav.length === 0 ? (
        <p className={styles.muted}>No published wiki yet. On Overview, click Explain this repo after code indexing finishes.</p>
      ) : (
        <div className={styles.split}>
          <ul className={styles.tree}>
            {nav.map((item) => (
              <li key={item.slug} style={{ marginLeft: item.parent_slug ? "0.85rem" : 0 }}>
                <button
                  type="button"
                  className={styles.rowBtn}
                  data-active={page?.slug === item.slug}
                  onClick={() => void open(item.slug)}
                >
                  <strong>{item.title}</strong>
                  <span>{item.type}</span>
                </button>
              </li>
            ))}
          </ul>
          <article className={styles.detail}>
            {page ? (
              <>
                <h3>{page.title}</h3>
                <p className={styles.muted}>
                  {page.validation_status === "warn" ? "Published with warnings" : "Published"} · {page.type}
                </p>
                {page.warnings.length > 0 ? (
                  <p className={styles.muted}>{page.warnings.join(" · ")}</p>
                ) : null}
                <Markdown>{page.body}</Markdown>
                {page.diagram_mermaid ? (
                  <pre className={styles.muted}>{page.diagram_mermaid}</pre>
                ) : null}
                {page.sources.length > 0 ? (
                  <>
                    <p className={styles.cat}>Source</p>
                    <ul className={styles.plainList}>
                      {page.sources.slice(0, 12).map((source) => (
                        <li key={source.path}>
                          <a className={styles.fileLink} href={githubBlob(repo, source.path, source.start_line || undefined, source.end_line || undefined)} target="_blank" rel="noreferrer">
                            <code>{source.path}</code>
                          </a>
                        </li>
                      ))}
                    </ul>
                  </>
                ) : null}
                <div className={styles.featureBlock}>
                  <button type="button" className={styles.primary} onClick={onOpenArchitecture}>
                    Open architecture
                  </button>
                  <button
                    type="button"
                    className={styles.primary}
                    onClick={() => onAsk(`Explain ${page.title} and point me at the source.`, context)}
                  >
                    Ask about this page
                  </button>
                </div>
              </>
            ) : (
              <p className={styles.muted}>Select a page.</p>
            )}
          </article>
        </div>
      )}
    </section>
  );
}
