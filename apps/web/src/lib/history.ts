export type OwnershipEntry = {
  author_id: string;
  author: string | null;
  email: string | null;
  github_login: string | null;
  path_prefix: string;
  score: number;
  share: number | null;
  last_touched_at: string | null;
};

export type BusFactorEntry = {
  path_prefix: string;
  author_id: string;
  share: number;
  score: number;
  inactive: boolean;
  last_seen_at: string | null;
};

export type ContributionEntry = {
  author_id: string;
  email: string;
  name: string | null;
  github_login: string | null;
  last_seen_at: string | null;
  commit_count: number;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001";

export function clientApiBase(): string {
  return API_BASE;
}

export async function fetchOwnership(repoId: string): Promise<OwnershipEntry[]> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/ownership`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchBusFactor(repoId: string): Promise<BusFactorEntry[]> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/bus-factor`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchContributions(repoId: string): Promise<ContributionEntry[]> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/contributions`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerIndexHistory(
  repoId: string,
): Promise<{ message: string; task_id: string | null }> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/index-history`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerReembed(
  repoId: string,
): Promise<{ message: string; task_id: string | null }> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/reembed`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
