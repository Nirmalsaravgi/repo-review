const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8001";

export type BriefDomain = { name: string; folders: string[]; why: string };
export type BriefHotspot = {
  path: string;
  symbol_count: number;
  domain: string | null;
  layer: string | null;
};

export type Brief = {
  summary: string;
  domains: BriefDomain[];
  architecture_layers: string[];
  suggested_questions: string[];
  languages: Record<string, number>;
  frameworks: string[];
  file_count: number;
  loc: number;
  externals: { name?: string; kind?: string; evidence?: string[] }[];
  entry_points: { path?: string; kind?: string; name?: string }[];
  endpoint_count: number;
  hotspots: BriefHotspot[];
  indexed_sha: string | null;
  source: string;
};

export type ArchNode = {
  id: string;
  label: string;
  layer_name: string;
  domain: string;
  symbol_count: number;
  file_count: number;
  layer: number;
  x: number;
  y: number;
  folders: string[];
};

export type ArchEdge = { src: string; dst: string; weight: number; confidence: number };

export type Architecture = { nodes: ArchNode[]; edges: ArchEdge[] };

export type EndpointItem = {
  id: string | null;
  method: string;
  path: string;
  group: string;
  handler_name: string | null;
  file_path: string | null;
  auth_hint: string;
  source: string;
};

export type EndpointGroups = { groups: Record<string, EndpointItem[]>; total: number };

export type FlowSummary = {
  id: string;
  title: string;
  kind: string;
  step_count: number;
  handler_name: string | null;
};

export type FlowStep = {
  src: string;
  dst: string;
  kind: string;
  confidence: number;
  depth: number;
};

export type FlowDetail = {
  id: string;
  title: string;
  kind: string;
  steps: FlowStep[];
  mermaid: string | null;
  explanation: string | null;
  files: string[];
  seed_symbol: string | null;
};

export type FlowQuery = FlowDetail & {
  matched: boolean;
  question: string;
  retrieved_files: string[];
  note: string | null;
};

export type FileMember = { path: string; symbol_count: number; start_line: number };
export type SymbolMember = {
  name: string;
  kind: string;
  path: string;
  start_line: number;
  end_line: number;
};
export type ComponentMembers = {
  component_id: string;
  label: string;
  layer: string;
  domain: string | null;
  files: FileMember[];
  symbols: SymbolMember[];
};

export type ImpactItem = {
  name: string;
  path: string;
  kind: string;
  depth: number;
  confidence: number;
};

export type FrameworkHit = { name: string; confidence: number; evidence: string[] };

export type RepoProfile = {
  application_style: string;
  languages: Record<string, number>;
  frameworks: FrameworkHit[];
  analyzers: string[];
  signals: { detector?: string; weight?: number; detail?: unknown; style?: string }[];
  confidence: number;
  indexed_sha: string | null;
};

export type ModuleNode = {
  id: string;
  parent_id: string | null;
  name: string;
  kind: string;
  cohesion: number;
  coupling: number;
  file_count: number;
  symbol_count: number;
  evidence: {
    method?: string;
    files?: string[];
    folders?: string[];
    reason?: string;
    boundary?: string | null;
    child_modules?: string[];
    intra_weight?: number;
    cross_weight?: number;
  };
  summary: string | null;
  children: ModuleNode[];
};

export type FeatureItem = {
  id: string;
  name: string;
  confidence: number;
  module_ids: string[];
  entry_point_symbol_ids: string[];
  evidence: {
    path?: string[];
    folders?: string[];
    modules?: string[];
    resolution?: string;
    entry?: { title?: string; kind?: string; file?: string | null };
  };
  summary: string | null;
};

export type Decomposition = { modules: ModuleNode[]; features: FeatureItem[] };

export const STYLE_LABEL: Record<string, string> = {
  backend_api: "Backend API",
  frontend: "Frontend application",
  fullstack_monorepo: "Full-stack monorepo",
  ml_system: "ML / AI system",
  cli: "Command-line tool",
  library: "Library / SDK",
  event_driven: "Event-driven system",
  infra: "Infrastructure",
  data_pipeline: "Data pipeline",
  unknown: "Unknown — every analyzer",
};

export type ComponentImpact = {
  component_id: string;
  label: string;
  layer: string;
  domain: string | null;
  risk: string;
  summary: string;
  member_count: number;
  total: number;
  by_category: Record<string, ImpactItem[]>;
  endpoints: { method: string; path: string; file_path: string }[];
  note: string | null;
};

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<T>;
}

export function fetchBrief(repoId: string) {
  return getJson<Brief>(`/repos/${repoId}/brief`);
}

export function fetchArchitecture(repoId: string) {
  return getJson<Architecture>(`/repos/${repoId}/architecture`);
}

export function fetchProfile(repoId: string) {
  return getJson<RepoProfile>(`/repos/${repoId}/profile`);
}

export function fetchDecomposition(repoId: string) {
  return getJson<Decomposition>(`/repos/${repoId}/decomposition`);
}

export type WikiNavItem = { slug: string; title: string; type: string; parent_slug: string | null };
export type WikiSource = { path: string; start_line: number | null; end_line: number | null };
export type WikiPage = {
  slug: string;
  title: string;
  type: string;
  body: string;
  parent_slug: string | null;
  sources: WikiSource[];
  validation_status: string;
  warnings: string[];
  diagram_mermaid: string | null;
  related: WikiNavItem[];
  indexed_sha: string | null;
};

export function fetchWiki(repoId: string) {
  return getJson<{ pages: WikiNavItem[] }>(`/repos/${repoId}/wiki`);
}

export function fetchWikiPage(repoId: string, slug: string) {
  return getJson<WikiPage>(`/repos/${repoId}/wiki/${slug.split("/").map(encodeURIComponent).join("/")}`);
}

export function fetchEndpoints(repoId: string) {
  return getJson<EndpointGroups>(`/repos/${repoId}/endpoints`);
}

export function fetchFlows(repoId: string) {
  return getJson<FlowSummary[]>(`/repos/${repoId}/flows`);
}

export function fetchFlow(repoId: string, flowId: string) {
  return getJson<FlowDetail>(`/repos/${repoId}/flows/${flowId}`);
}

export async function queryFlow(repoId: string, question: string): Promise<FlowQuery> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/flows/query`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, persist: true }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<FlowQuery>;
}

export function fetchComponentMembers(repoId: string, componentId: string) {
  return getJson<ComponentMembers>(
    `/repos/${repoId}/architecture/members?component=${encodeURIComponent(componentId)}`,
  );
}

export function fetchComponentImpact(repoId: string, componentId: string) {
  return getJson<ComponentImpact>(
    `/repos/${repoId}/impact?component=${encodeURIComponent(componentId)}`,
  );
}

export function githubBlob(
  repo: { full_name: string; last_indexed_sha: string | null; default_branch: string },
  path: string,
  startLine?: number,
  endLine?: number,
) {
  const ref = repo.last_indexed_sha || repo.default_branch;
  const base = `https://github.com/${repo.full_name}/blob/${ref}/${path}`;
  if (!startLine) return base;
  if (endLine && endLine !== startLine) return `${base}#L${startLine}-L${endLine}`;
  return `${base}#L${startLine}`;
}

export async function triggerIndexUnderstanding(
  repoId: string,
): Promise<{ message: string; task_id: string | null }> {
  const res = await fetch(`${API_BASE}/repos/${repoId}/index-understanding`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export function isFlowQuestion(question: string): boolean {
  const t = question.trim().toLowerCase();
  return (
    /\bhow (does|do|is|are)\b/.test(t) ||
    /\bwhat happens when\b/.test(t) ||
    /\bwhat does\b/.test(t) ||
    /\bend to end\b/.test(t)
  );
}
export function architectureToModuleGraph(arch: Architecture) {
  return {
    nodes: arch.nodes.map((n) => ({
      id: n.id,
      label: n.label,
      symbol_count: n.symbol_count,
      layer: n.layer,
      x: n.x,
      y: n.y,
    })),
    edges: arch.edges,
  };
}
