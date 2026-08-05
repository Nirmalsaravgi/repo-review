import { apiFetch, type Repository, type Session } from "@/lib/api";
import { RepoPicker } from "@/components/RepoPicker";
import { LoginPanel } from "@/components/LoginPanel";
import styles from "./page.module.css";

export const dynamic = "force-dynamic";

async function getSession(): Promise<Session> {
  try {
    return await apiFetch<Session>("/auth/me", { cache: "no-store" });
  } catch {
    return {
      authenticated: false,
      user: null,
      org: null,
      github_configured: false,
      install_url: null,
    };
  }
}

async function getRepos(authenticated: boolean): Promise<Repository[]> {
  if (!authenticated) return [];
  try {
    return await apiFetch<Repository[]>("/repos", { cache: "no-store" });
  } catch {
    return [];
  }
}

export default async function HomePage() {
  const session = await getSession();
  const repos = await getRepos(session.authenticated);

  return (
    <main className={styles.shell}>
      <header className={styles.header}>
        <div>
          <p className={styles.brand}>Repo Understanding</p>
          <p className={styles.tagline}>Code questions, structure maps, git intelligence</p>
        </div>
        {session.authenticated && session.user ? (
          <div className={styles.user}>
            <span className={styles.muted}>Signed in as</span>
            <strong>{session.user.login}</strong>
            {session.org ? <span className={styles.muted}>· {session.org.name}</span> : null}
          </div>
        ) : null}
      </header>

      {!session.authenticated ? (
        <LoginPanel session={session} />
      ) : (
        <RepoPicker initialRepos={repos} installUrl={session.install_url} />
      )}
    </main>
  );
}
