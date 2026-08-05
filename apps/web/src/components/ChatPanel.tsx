"use client";

import { useRef, useState } from "react";
import type { Repository } from "@/lib/api";
import styles from "./ChatPanel.module.css";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001";

type Citation = { path: string; start_line: number; end_line: number };
type Turn = { role: "user" | "assistant"; content: string; citations?: Citation[] };

export function ChatPanel({ repo }: { repo: Repository }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const conversationId = useRef<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  function newChat() {
    if (busy) return;
    conversationId.current = null;
    setTurns([]);
    setError(null);
    setStatus(null);
  }

  function updateLast(fn: (t: Turn) => Turn) {
    setTurns((prev) => {
      if (prev.length === 0) return prev;
      const next = prev.slice();
      next[next.length - 1] = fn(next[next.length - 1]);
      return next;
    });
    queueMicrotask(() =>
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }),
    );
  }

  function onEvent(event: string, data: Record<string, unknown>) {
    if (event === "meta") {
      conversationId.current = (data.conversation_id as string) || conversationId.current;
    } else if (event === "tool_start") {
      setStatus(`${data.name}(${argPreview(data.arguments)})…`);
    } else if (event === "tool_end") {
      setStatus(`${data.name}: ${data.summary}`);
    } else if (event === "delta") {
      updateLast((t) => ({ ...t, content: t.content + (data.text as string) }));
    } else if (event === "done") {
      conversationId.current =
        (data.conversation_id as string) || conversationId.current;
      updateLast((t) => ({
        ...t,
        content: data.text as string,
        citations: (data.citations as Citation[]) || [],
      }));
      setStatus(data.cached ? "answered from cache" : null);
    } else if (event === "error") {
      setError((data.message as string) || "Chat failed");
    }
  }

  async function ask() {
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setError(null);
    setTurns((prev) => [
      ...prev,
      { role: "user", content: question },
      { role: "assistant", content: "" },
    ]);
    setBusy(true);
    setStatus("Thinking…");
    try {
      const res = await fetch(`${API_BASE}/repos/${repo.id}/chat`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          conversation_id: conversationId.current,
        }),
      });
      if (!res.ok || !res.body) throw new Error(await res.text());
      await readSse(res.body, onEvent);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Chat failed");
    } finally {
      setBusy(false);
      setStatus(null);
    }
  }

  return (
    <section className={styles.panel}>
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Ask about {repo.full_name}</h2>
          <span className={styles.hint}>
            Answers are grounded in the cloned code, with line citations.
          </span>
        </div>
        <button
          type="button"
          className={styles.newChat}
          onClick={newChat}
          disabled={busy || turns.length === 0}
        >
          New chat
        </button>
      </header>

      <div className={styles.log} ref={scrollRef}>
        {turns.length === 0 ? (
          <p className={styles.empty}>
            Try: “Where is authentication handled?” or “What does the clone service do?”
          </p>
        ) : (
          turns.map((t, i) => (
            <div key={i} className={t.role === "user" ? styles.user : styles.assistant}>
              <div className={styles.bubble}>
                {t.content || (busy && i === turns.length - 1 ? "…" : "")}
              </div>
              {t.citations && t.citations.length > 0 ? (
                <ul className={styles.citations}>
                  {t.citations.map((c, j) => (
                    <li key={j}>
                      <a
                        href={citationUrl(repo, c)}
                        target="_blank"
                        rel="noreferrer"
                        className={styles.cite}
                      >
                        {c.path}:{c.start_line}-{c.end_line}
                      </a>
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          ))
        )}
      </div>

      {status ? <div className={styles.status}>{status}</div> : null}
      {error ? <div className={styles.error}>{error}</div> : null}

      <form
        className={styles.composer}
        onSubmit={(e) => {
          e.preventDefault();
          ask();
        }}
      >
        <input
          className={styles.input}
          placeholder="Ask a question about this repository…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={busy}
        />
        <button type="submit" className={styles.send} disabled={busy || !input.trim()}>
          {busy ? "…" : "Ask"}
        </button>
      </form>
    </section>
  );
}

function argPreview(args: unknown): string {
  if (!args || typeof args !== "object") return "";
  const parts = Object.values(args as Record<string, unknown>)
    .map((v) => String(v))
    .filter(Boolean);
  return parts.join(", ").slice(0, 60);
}

function citationUrl(repo: Repository, c: Citation): string {
  const ref = repo.last_indexed_sha || repo.default_branch;
  return `https://github.com/${repo.full_name}/blob/${ref}/${c.path}#L${c.start_line}-L${c.end_line}`;
}

/** Parse an SSE stream from a fetch Response body. */
async function readSse(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: Record<string, unknown>) => void,
) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf = (buf + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue; // comment/ping frame
      try {
        onEvent(event, JSON.parse(dataLines.join("\n")));
      } catch {
        // ignore malformed frame
      }
    }
  }
}
