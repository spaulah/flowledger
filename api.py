"""HTTP API for FlowLedger.

Run from the project root:

    uvicorn api:app --host 127.0.0.1 --port 8000

Interactive docs: http://127.0.0.1:8000/docs

The API answers questions from the index built by jobs/reindex_local.py. It has no user
accounts: it serves everything in the index to anyone who can reach it. Keep it bound to
127.0.0.1, or set FLOWLEDGER_API_KEY to require an X-API-Key header on /ask.
"""
import logging
import os
import secrets
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from logging_config import configure_logging

load_dotenv()
configure_logging()
logger = logging.getLogger(__name__)


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000,
                          examples=["Which flows write to SharePoint lists?"])
    k: int = Field(8, ge=1, le=20, description="How many indexed chunks to retrieve as context.")


class Source(BaseModel):
    flow_name: Optional[str] = None
    type: Optional[str] = Field(None, description="definition, run_summary, or run_action")
    action_name: Optional[str] = None
    snippet: str


class AskResponse(BaseModel):
    answer: str
    sources: list[Source]


class Health(BaseModel):
    status: str
    index_ready: bool


def build_ai_service():
    """Build the real service. Imported here so importing this module stays cheap."""
    from ai_analysis_service import AIAnalysisService
    from rag_service import RAGService, chroma_path

    rag = RAGService(persist_directory=chroma_path())
    return AIAnalysisService(rag_service=rag)


def require_api_key(x_api_key: Optional[str] = Header(default=None)):
    """If FLOWLEDGER_API_KEY is set, the request must send the same value in X-API-Key."""
    expected = os.getenv("FLOWLEDGER_API_KEY")
    if not expected:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key header.")


def create_app(ai_service=None) -> FastAPI:
    """Create the app. Pass `ai_service` to use a stand-in, for example in tests."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = ai_service or build_ai_service()
        yield

    app = FastAPI(
        title="FlowLedger API",
        version="0.1.0",
        description="Ask questions about your Power Automate flows and their run history.",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=Health)
    def health(request: Request):
        return Health(status="ok", index_ready=request.app.state.service.rag_service.has_documents())

    # Plain `def`, not `async def`: the model and embedding calls block, so FastAPI runs this in a thread pool.
    @app.post("/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
    def ask(body: AskRequest, request: Request):
        service = request.app.state.service
        if not service.rag_service.has_documents():
            raise HTTPException(status_code=503,
                                detail="The index is empty. Run `python jobs/reindex_local.py` first.")
        try:
            answer, docs = service.ask_sync(body.question, k=body.k)
        except Exception:
            # Details go to the server log only. They can contain endpoint URLs.
            logger.exception("Failed to answer the question")
            raise HTTPException(status_code=502,
                                detail="The chat or embedding service failed. Check the server log.")

        sources = [
            Source(
                flow_name=d["metadata"].get("flow_name"),
                type=d["metadata"].get("type"),
                action_name=d["metadata"].get("action_name"),
                snippet=d["snippet"],
            )
            for d in docs
        ]
        return AskResponse(answer=answer, sources=sources)

    return app


app = create_app()
