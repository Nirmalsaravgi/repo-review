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