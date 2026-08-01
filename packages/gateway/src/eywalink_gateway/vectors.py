"""Qdrant vector search integration.

Exposes a small retrieval API so the reference implementation shows the
full RAG pattern: embeddings stored in Qdrant, retrieved over HTTP.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/v1/vectors", tags=["vectors"])


class SearchRequest(BaseModel):
    collection: str
    vector: list[float]
    limit: int = 5


@router.get("/collections")
async def list_collections(request: Request) -> dict:
    client = getattr(request.app.state, "qdrant", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Qdrant not configured")
    try:
        collections = client.get_collections().collections
        return {"collections": [c.name for c in collections]}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Qdrant error: {exc}") from exc


@router.post("/search")
async def search(req: SearchRequest, request: Request) -> dict:
    client = getattr(request.app.state, "qdrant", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Qdrant not configured")
    try:
        hits = client.search(
            collection_name=req.collection,
            query_vector=req.vector,
            limit=req.limit,
        )
        return {
            "hits": [
                {"id": str(h.id), "score": h.score, "payload": h.payload}
                for h in hits
            ]
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Qdrant error: {exc}") from exc
