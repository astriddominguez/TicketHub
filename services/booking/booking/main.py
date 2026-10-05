from typing import Annotated

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from booking.db import get_session

app = FastAPI(
    title="TicketHub Booking API",
    description="Temporary reservations that never sell the same ticket twice.",
    version="0.1.0",
)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@app.get("/health", tags=["ops"])
async def health(session: SessionDep) -> dict[str, str]:
    """Liveness + database check, for Docker/Kubernetes health probes."""
    await session.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}
