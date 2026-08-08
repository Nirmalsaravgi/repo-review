import { cookies } from "next/headers";

const API_BASE =
  process.env.API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://localhost:8001";

export type Session = {
  authenticated: boolean;
  user: {
    id: string;
    org_id: string;
    github_user_id: number;
    email: string | null;
    login: string;
    avatar_url: string | null;
  } | null;
  org: { id: string; name: string; github_org_id: number | null } | null;
  github_configured: boolean;
  install_url: string | null;
};

export type Repository = {
  id: string;
  org_id: string;
  github_repo_id: number;
  full_name: string;
  default_branch: string;
  private: boolean;
  index_status: string;
  index_error: string | null;
  last_indexed_sha: string | null;
  indexed_at: string | null;
  is_shallow: boolean;
  selected: boolean;
  clone_path: string | null;
  head_sha?: string | null;
  index_fresh?: boolean;
};

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");

  const url = `${API_BASE}${path}`;
  const res = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(cookieHeader ? { Cookie: cookieHeader } : {}),
      ...(init?.headers || {}),
    },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export function apiBase(): string {
  return API_BASE;
}
