"""Scoring routes.

Score history is served from the company router as
`GET /companies/{slug}/score-history`, since it is always read in the context of
a company. This router exists for scoring-wide endpoints added later.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/scoring", tags=["scores"])
