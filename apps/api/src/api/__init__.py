"""FastAPI application entrypoint."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import auth, chat, graph, health, history, pr_reviews, repos, webhooks
from repo_core.config import get_settings


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.clone_root.mkdir(parents=True, exist_ok=True)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Repo Understanding API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(repos.router, prefix="/repos", tags=["repos"])
    app.include_router(history.router, prefix="/repos", tags=["history"])
    app.include_router(chat.router, prefix="/repos", tags=["chat"])
    app.include_router(graph.router, prefix="/repos", tags=["graph"])
    app.include_router(pr_reviews.router, prefix="/repos", tags=["pr-reviews"])
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
    return app


app = create_app()
