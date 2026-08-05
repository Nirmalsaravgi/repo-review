"use client";

import styles from "./LoginPanel.module.css";
import type { Session } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001";

export function LoginPanel({ session }: { session: Session }) {
  async function startLogin() {
    const res = await fetch(`${API_BASE}/auth/login`, { credentials: "include" });
    if (!res.ok) {
      const body = await res.text();
      alert(body || "Login unavailable — configure the GitHub App first.");
      return;
    }
    const data = (await res.json()) as { url: string };
    window.location.href = data.url;
  }

  return (
    <section className={styles.panel}>
      <h1 className={styles.title}>Connect a GitHub repository</h1>
      <p className={styles.copy}>
        Sign in with GitHub, install the App on your org or account, then pick a repo to
        clone. Phase 0 starts with a shallow clone and agentic chat — no index yet.
      </p>

      {!session.github_configured ? (
        <div className={styles.warn}>
          GitHub App env vars are not configured on the API. Copy{" "}
          <code>.env.example</code> → <code>.env</code>, add App credentials and{" "}
          <code>private-key.pem</code>, then restart the API.
        </div>
      ) : null}

      <div className={styles.actions}>
        <button type="button" className={styles.primary} onClick={startLogin}>
          Continue with GitHub
        </button>
        {session.install_url ? (
          <a className={styles.secondary} href={session.install_url}>
            Install GitHub App
          </a>
        ) : null}
      </div>
    </section>
  );
}
