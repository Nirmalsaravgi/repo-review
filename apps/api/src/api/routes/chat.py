"""Streaming chat endpoint + conversation history — the agent loop over a clone."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from repo_core.config import get_settings
from repo_core.db import SessionLocal
from repo_core.models import ChatMessage, Conversation, IndexStatus, Repository
from repo_core.schemas import (
    ChatRequest,
    ConversationDetailOut,
    ConversationOut,
)
from repo_core.session import SessionData
from repo_providers import Message, ProviderError, get_llm_provider
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sse_starlette.sse import EventSourceResponse

from api.agent import (
    Agent,
    AnswerCompleted,
    AnswerDelta,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from api.agent.cache import RedisResponseCache
from api.deps import require_session, tenant_db
from api.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/{repo_id}/chat")
async def chat(
    repo_id: str,
    body: ChatRequest,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> EventSourceResponse:
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question must not be empty")

    repo = await _load_repo(db, session, repo_id)
    if repo.index_status != IndexStatus.READY.value:
        raise HTTPException(
            status_code=409,
            detail=f"Repository is not ready to query (status: {repo.index_status})",
        )
    if not repo.clone_path or not Path(repo.clone_path).exists():
        raise HTTPException(status_code=409, detail="Repository clone is missing on disk")

    settings = get_settings()
    if not settings.llm_provider:
        raise HTTPException(status_code=503, detail="LLM provider is not configured")

    # Resolve (or create) the conversation and rebuild history from the DB — the
    # server is the source of truth, not the client.
    conversation, history = await _resolve_conversation(db, session, repo, body, question)
    db.add(
        ChatMessage(
            id=uuid4(),
            org_id=session.org_uuid,
            conversation_id=conversation.id,
            role="user",
            content=question,
        )
    )
    await db.commit()

    # Extract plain values now — the DB session won't outlive the stream.
    org_id = session.org_uuid
    conversation_id = conversation.id
    root = Path(repo.clone_path)
    repo_sha = repo.last_indexed_sha or ""
    repo_full_name = repo.full_name
    repo_uuid = repo.id

    async def event_stream() -> AsyncIterator[dict[str, Any]]:
        yield {"event": "meta", "data": json.dumps({"conversation_id": str(conversation_id)})}
        try:
            provider = get_llm_provider(settings)
        except ProviderError as exc:
            yield _error(str(exc))
            return

        agent = Agent(
            provider=provider,
            root=root,
            repo_sha=repo_sha,
            repo_full_name=repo_full_name,
            org_id=org_id,
            repo_id=repo_uuid,
            redis=get_redis(),
            cache=RedisResponseCache(get_redis()),
        )
        try:
            async for event in agent.run(question, history):
                if isinstance(event, AnswerCompleted):
                    await _persist_answer(org_id, conversation_id, event)
                    yield _done(event, conversation_id)
                else:
                    yield _to_sse(event)
        except ProviderError as exc:
            yield _error(str(exc))
        except Exception:
            logger.exception("agent run failed for repo %s", repo_id)
            yield _error("The agent hit an internal error.")

    return EventSourceResponse(event_stream())


@router.get("/{repo_id}/conversations", response_model=list[ConversationOut])
async def list_conversations(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> list[ConversationOut]:
    repo = await _load_repo(db, session, repo_id)
    result = await db.execute(
        select(Conversation)
        .where(Conversation.org_id == session.org_uuid, Conversation.repo_id == repo.id)
        .order_by(Conversation.updated_at.desc())
    )
    return [ConversationOut.model_validate(c) for c in result.scalars().all()]


@router.get("/{repo_id}/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    repo_id: str,
    conversation_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> ConversationDetailOut:
    repo = await _load_repo(db, session, repo_id)
    result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(
            Conversation.org_id == session.org_uuid,
            Conversation.repo_id == repo.id,
            Conversation.id == conversation_id,
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conversation.messages.sort(key=lambda m: m.created_at)
    return ConversationDetailOut.model_validate(conversation)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _load_repo(db: AsyncSession, session: SessionData, repo_id: str) -> Repository:
    result = await db.execute(
        select(Repository).where(
            Repository.org_id == session.org_uuid,
            Repository.id == repo_id,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


async def _resolve_conversation(
    db: AsyncSession,
    session: SessionData,
    repo: Repository,
    body: ChatRequest,
    question: str,
) -> tuple[Conversation, list[Message]]:
    if body.conversation_id is not None:
        result = await db.execute(
            select(Conversation)
            .options(selectinload(Conversation.messages))
            .where(
                Conversation.org_id == session.org_uuid,
                Conversation.repo_id == repo.id,
                Conversation.id == body.conversation_id,
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        history = _to_history(sorted(conversation.messages, key=lambda m: m.created_at))
        return conversation, history

    conversation = Conversation(
        id=uuid4(),
        org_id=session.org_uuid,
        repo_id=repo.id,
        title=_title(question),
    )
    db.add(conversation)
    await db.flush()
    return conversation, []


def _to_history(messages: list[ChatMessage]) -> list[Message]:
    history: list[Message] = []
    for m in messages:
        if m.role == "user":
            history.append(Message.user(m.content))
        elif m.role == "assistant":
            history.append(Message.assistant(text=m.content))
    return history


def _title(question: str) -> str:
    first = question.strip().splitlines()[0] if question.strip() else "New conversation"
    return first[:120]


async def _persist_answer(org_id: UUID, conversation_id: UUID, event: AnswerCompleted) -> None:
    """Persist the assistant turn in a fresh session (the request session is gone)."""
    async with SessionLocal() as db:
        await db.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(org_id)},
        )
        db.add(
            ChatMessage(
                id=uuid4(),
                org_id=org_id,
                conversation_id=conversation_id,
                role="assistant",
                content=event.text,
                citations=[c.__dict__ for c in event.citations],
            )
        )
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.updated_at = datetime.now(UTC)
        await db.commit()


def _to_sse(event: Any) -> dict[str, Any]:
    if isinstance(event, StepStarted):
        return {"event": "step", "data": json.dumps({"step": event.step})}
    if isinstance(event, ToolStarted):
        return {
            "event": "tool_start",
            "data": json.dumps({"id": event.id, "name": event.name, "arguments": event.arguments}),
        }
    if isinstance(event, ToolFinished):
        return {
            "event": "tool_end",
            "data": json.dumps(
                {"id": event.id, "name": event.name, "ok": event.ok, "summary": event.summary}
            ),
        }
    if isinstance(event, AnswerDelta):
        return {"event": "delta", "data": json.dumps({"text": event.text})}
    return {"event": "message", "data": json.dumps({})}


def _done(event: AnswerCompleted, conversation_id: UUID) -> dict[str, Any]:
    return {
        "event": "done",
        "data": json.dumps(
            {
                "text": event.text,
                "citations": [c.__dict__ for c in event.citations],
                "steps": event.steps,
                "cached": event.cached,
                "usage": event.usage.__dict__ if event.usage else None,
                "conversation_id": str(conversation_id),
            }
        ),
    }


def _error(message: str) -> dict[str, Any]:
    return {"event": "error", "data": json.dumps({"message": message})}
