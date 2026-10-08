"""
Evidence spans: the exact text a claim or finding rests on.

Every claim and finding references spans, so the system can always answer
"why did you flag this?" with the original retrieved text. A claim from an
LLM that cannot be matched to a span is discarded.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class EvidenceField(str, Enum):
    TITLE = "title"
    SNIPPET = "snippet"
    RICH_SNIPPET = "rich_snippet"
    DATE = "date"
    ANSWER_BOX = "answer_box"


class EvidenceSpan(BaseModel):
    id: str
    result_id: str
    source_id: str | None
    field: EvidenceField
    text: str  # exact substring of the field
    start: int  # char offset within the field
    end: int
