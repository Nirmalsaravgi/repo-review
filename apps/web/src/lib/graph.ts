const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001";

export type ModuleNode = {
  id: string;
  label: string;
  symbol_count: number;
  layer: number;
  x: number;
  y: number;
};

export type ModuleEdge = {
  src: string;
  dst: string;
  weight: number;
  confidence: number;
};

export type ModuleGraph = {
  nodes: ModuleNode[];
  edges: ModuleEdge[];
};

export type ImpactItem = {
  name: string;
  path: string;
  kind: string;
  depth: number;
  confidence: number;
};

export type BlastRadius = {
  symbol: string;
  target: { name: string; path: string; kind: string } | null;
  total: number;
  by_category: Record<string, ImpactItem[]>;
  note: string | null;
};

export type CallFlowStep = {
  src: string;
  dst: string;
  kind: string;
  confidence: number;
  depth: number;
};

export type CallFlow = {
  symbol: string;
  target: { name: string; path: string; kind: string } | null;
  steps: CallFlowStep[];
  mermaid: string | null;
  note: string | null;
};

export async function fetchModuleGraph(repoId: string, depth = 2): Promise<ModuleGraph> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/graph/modules?depth=${depth}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchBlastRadius(repoId: string, symbol: string): Promise<BlastRadius> {
  const res = await fetch(
    `${API_BASE}/repos/${repoId}/graph/blast?symbol=${encodeURIComponent(symbol)}`,
    { credentials: "include" },
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function fetchCallFlow(repoId: string, symbol: string): Promise<CallFlow> {
  const res = await fetch(
    `${API_BASE}/repos/${repoId}/graph/callflow?symbol=${encodeURIComponent(symbol)}`,
    { credentials: "include" },
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function triggerIndexGraph(
  repoId: string,
): Promise<{ message: string; task_id: string | null }> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/index-graph`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

/** Parse symbols + embed + build call graph (what users need when the map is empty). */
export async function triggerIndexCode(
  repoId: string,
): Promise<{ message: string; task_id: string | null }> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/index-code`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
