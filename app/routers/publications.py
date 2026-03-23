"""Router Publications – recherche et consultation des appels d'offres."""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_

from app.utils.db import get_db
from app.models.publication import Publication
from app.schemas.publication import PublicationResponse

router = APIRouter(prefix="/publications", tags=["Publications"])


@router.get("/search", response_model=List[PublicationResponse])
async def search_publications(
    query: Optional[str] = Query(None, description="Recherche texte libre"),
    source: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    min_budget: Optional[float] = Query(None),
    max_budget: Optional[float] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Recherche avancée dans les publications."""
    stmt = select(Publication).order_by(Publication.created_at.desc())

    filters = []

    if query:
        search = f"%{query}%"
        filters.append(
            or_(
                Publication.title.ilike(search),
                Publication.summary.ilike(search),
                Publication.reference.ilike(search),
            )
        )

    if source:
        filters.append(Publication.source == source)

    if category:
        filters.append(Publication.category == category)

    if min_budget is not None:
        filters.append(Publication.budget >= min_budget)

    if max_budget is not None:
        filters.append(Publication.budget <= max_budget)

    if filters:
        stmt = stmt.where(and_(*filters))

    offset = (page - 1) * per_page
    stmt = stmt.offset(offset).limit(per_page)

    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{publication_id}", response_model=PublicationResponse)
async def get_publication(
    publication_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Récupère les détails d'une publication."""
    result = await db.execute(
        select(Publication).where(Publication.id == publication_id)
    )
    pub = result.scalar_one_or_none()
    if not pub:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Publication non trouvée")
    return pub


@router.get("/sources/list")
async def list_sources(db: AsyncSession = Depends(get_db)):
    """Liste toutes les sources disponibles."""
    result = await db.execute(
        select(Publication.source).distinct()
    )
    sources = [row[0] for row in result.all()]
    return {"sources": sources}


@router.get("/stats/summary")
async def publications_stats(db: AsyncSession = Depends(get_db)):
    """Statistiques sur les publications."""
    from sqlalchemy import func

    total = await db.execute(select(func.count(Publication.id)))
    by_source = await db.execute(
        select(Publication.source, func.count(Publication.id))
        .group_by(Publication.source)
    )
    by_category = await db.execute(
        select(Publication.category, func.count(Publication.id))
        .group_by(Publication.category)
    )

    return {
        "total": total.scalar(),
        "by_source": dict(by_source.all()),
        "by_category": dict(by_category.all()),
    }
