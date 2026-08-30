from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(ORMModel):
    id: UUID
    org_id: UUID
    github_user_id: int
    email: str | None
    login: str
    avatar_url: str | None


class OrgOut(ORMModel):
    id: UUID
    name: str
    github_org_id: int | None


class RepositoryOut(ORMModel):
    id: UUID
    org_id: UUID
    github_repo_id: int
    full_name: str
    default_branch: str
    private: bool
    index_status: str
    index_error: str | None
    last_indexed_sha: str | None
    indexed_at: datetime | None
    is_shallow: bool
    selected: bool
    clone_path: str | None = None
    # Phase 2 P6 — working-tree tip vs last successful code index
    head_sha: str | None = None
    index_fresh: bool = True


class SessionOut(BaseModel):
    authenticated: bool
    user: UserOut | None = None
    org: OrgOut | None = None
    github_configured: bool
    install_url: str | None = None


class SelectRepoRequest(BaseModel):
    github_repo_id: int = Field(..., description="GitHub repository numeric id")


class HealthOut(BaseModel):
    status: str
    github_configured: bool
    database: str


class AuthUrlOut(BaseModel):
    url: str


class MessageOut(BaseModel):
    message: str


class ChatRequest(BaseModel):
    question: str
    conversation_id: UUID | None = None


class CitationOut(BaseModel):
    path: str
    start_line: int
    end_line: int


class ChatMessageOut(ORMModel):
    id: UUID
    role: str
    content: str
    citations: list[CitationOut] | None = None
    created_at: datetime


class ConversationOut(ORMModel):
    id: UUID
    repo_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: list[ChatMessageOut] = Field(default_factory=list)


class OwnershipEntryOut(BaseModel):
    author_id: str
    author: str | None = None
    email: str | None = None
    github_login: str | None = None
    path_prefix: str
    score: float
    share: float | None = None
    last_touched_at: str | None = None


class BusFactorOut(BaseModel):
    path_prefix: str
    author_id: str
    share: float
    score: float
    inactive: bool
    last_seen_at: str | None = None


class ContributionOut(BaseModel):
    author_id: str
    email: str
    name: str | None = None
    github_login: str | None = None
    last_seen_at: str | None = None
    commit_count: int


class IndexHistoryOut(BaseModel):
    message: str
    task_id: str | None = None


class IndexCodeOut(BaseModel):
    message: str
    task_id: str | None = None


class IndexGraphOut(BaseModel):
    message: str
    task_id: str | None = None


# --------------------------------------------------------------------------- #
# Phase 3 — structure & visualization
# --------------------------------------------------------------------------- #
class ModuleNodeOut(BaseModel):
    id: str
    label: str
    symbol_count: int
    layer: int
    x: float
    y: float


class ModuleEdgeOut(BaseModel):
    src: str
    dst: str
    weight: int
    confidence: float


class ModuleGraphOut(BaseModel):
    nodes: list[ModuleNodeOut] = Field(default_factory=list)
    edges: list[ModuleEdgeOut] = Field(default_factory=list)


class ImpactItemOut(BaseModel):
    name: str
    path: str
    kind: str
    depth: int
    confidence: float


class BlastRadiusOut(BaseModel):
    symbol: str
    target: dict | None = None
    total: int = 0
    by_category: dict[str, list[ImpactItemOut]] = Field(default_factory=dict)
    note: str | None = None


class CallFlowStepOut(BaseModel):
    src: str
    dst: str
    kind: str
    confidence: float
    depth: int


class CallFlowOut(BaseModel):
    symbol: str
    target: dict | None = None
    steps: list[CallFlowStepOut] = Field(default_factory=list)
    mermaid: str | None = None
    note: str | None = None


class IndexUnderstandingOut(BaseModel):
    message: str
    task_id: str | None = None


class BriefDomainOut(BaseModel):
    name: str
    folders: list[str] = Field(default_factory=list)
    why: str = ""


class BriefHotspotOut(BaseModel):
    path: str
    symbol_count: int = 0
    domain: str | None = None
    layer: str | None = None


class BriefOut(BaseModel):
    summary: str
    domains: list[BriefDomainOut] = Field(default_factory=list)
    architecture_layers: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)
    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    file_count: int = 0
    loc: int = 0
    externals: list[dict] = Field(default_factory=list)
    entry_points: list[dict] = Field(default_factory=list)
    endpoint_count: int = 0
    hotspots: list[BriefHotspotOut] = Field(default_factory=list)
    indexed_sha: str | None = None
    source: str = "live"  # cached | live


class ArchitectureNodeOut(BaseModel):
    id: str
    label: str
    layer_name: str
    domain: str
    symbol_count: int
    file_count: int
    layer: int
    x: float
    y: float
    folders: list[str] = Field(default_factory=list)


class ArchitectureOut(BaseModel):
    nodes: list[ArchitectureNodeOut] = Field(default_factory=list)
    edges: list[ModuleEdgeOut] = Field(default_factory=list)


class EndpointOut(BaseModel):
    id: str | None = None
    method: str
    path: str
    group: str
    handler_name: str | None = None
    file_path: str | None = None
    auth_hint: str = "unknown"
    source: str = "decorator"


class EndpointGroupOut(BaseModel):
    groups: dict[str, list[EndpointOut]] = Field(default_factory=dict)
    total: int = 0


class FlowSummaryOut(BaseModel):
    id: str
    title: str
    kind: str
    step_count: int = 0
    handler_name: str | None = None


class FlowDetailOut(BaseModel):
    id: str
    title: str
    kind: str
    steps: list[CallFlowStepOut] = Field(default_factory=list)
    mermaid: str | None = None
    explanation: str | None = None
    files: list[str] = Field(default_factory=list)
    seed_symbol: str | None = None


class FlowQueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    persist: bool = True


class FlowQueryOut(BaseModel):
    id: str | None = None
    title: str
    kind: str
    steps: list[CallFlowStepOut] = Field(default_factory=list)
    mermaid: str | None = None
    explanation: str | None = None
    files: list[str] = Field(default_factory=list)
    seed_symbol: str | None = None
    matched: bool = False
    question: str = ""
    retrieved_files: list[str] = Field(default_factory=list)
    note: str | None = None


class FileMemberOut(BaseModel):
    path: str
    symbol_count: int = 0
    start_line: int = 1


class SymbolMemberOut(BaseModel):
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int


class ComponentMembersOut(BaseModel):
    component_id: str
    label: str
    layer: str
    domain: str | None = None
    files: list[FileMemberOut] = Field(default_factory=list)
    symbols: list[SymbolMemberOut] = Field(default_factory=list)



class ComponentImpactItemOut(BaseModel):
    name: str
    path: str
    kind: str
    depth: int
    confidence: float


class ComponentImpactOut(BaseModel):
    component_id: str
    label: str
    layer: str
    domain: str | None = None
    risk: str
    summary: str
    member_count: int = 0
    total: int = 0
    by_category: dict[str, list[ComponentImpactItemOut]] = Field(default_factory=dict)
    endpoints: list[dict] = Field(default_factory=list)
    note: str | None = None
