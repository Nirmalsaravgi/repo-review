import { apiFetch, type Repository, type Session } from "@/lib/api";
import { RepoPicker } from "@/components/RepoPicker";
import { LoginPanel } from "@/components/LoginPanel";
import { ThemeToggle } from "@/components/ThemeToggle";
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
        <div className={styles.brandRow}>
          <div className={styles.logo}>◇</div>
          <div>
            <p className={styles.brand}>Repo Understanding</p>
            <p className={styles.tagline}>Connect a repo · build a mental model · explore it</p>
          </div>
        </div>
        <div className={styles.headerRight}>
          <ThemeToggle />
          {session.authenticated && session.user ? (
            <div className={styles.user}>
              <div className={styles.userText}>
                <strong>{session.user.login}</strong>
                {session.org ? <span className={styles.muted}>{session.org.name}</span> : null}
              </div>
              {session.user.avatar_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img className={styles.avatar} src={session.user.avatar_url} alt={session.user.login} />
              ) : (
                <div className={styles.avatarFallback}>
                  {session.user.login.slice(0, 1).toUpperCase()}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </header>

      {!session.authenticated ? (
        <LoginPanel session={session} />
      ) : (
        <RepoPicker initialRepos={repos} installUrl={session.install_url} />
      )}
    </main>
  );
}
