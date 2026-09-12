"""Vendor-neutral document representation used by every ingestion backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ElementType = Literal[
    "title",
    "heading",
    "paragraph",
    "list_item",
    "table",
    "figure",
    "caption",
    "formula",
    "checkbox",
    "page_header",
    "page_footer",
    "page_number",
    "footnote",
]


@dataclass(frozen=True)
class BoundingBox:
    """Page-space coordinates for an extracted element."""

    x0: float
    y0: float
    x1: float
    y1: float

    def as_dict(self) -> dict[str, float]:
        return {
            "x0": round(self.x0, 2),
            "y0": round(self.y0, 2),
            "x1": round(self.x1, 2),
            "y1": round(self.y1, 2),
        }


@dataclass
class DocumentElement:
    """One typed, source-addressable element in a document."""

    element_id: str
    element_type: ElementType
    text: str
    page_number: int | None
    bbox: BoundingBox | None = None
    section_path: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedChunk:
    """Retrieval unit built from one or more related document elements."""

    content: str
    page_number: int | None
    chunk_index: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedDocument:
    """Complete parser result before persistence."""

    source_path: str
    source_sha256: str
    parser_backend: str
    page_count: int
    elements: list[DocumentElement]
    chunks: list[ParsedChunk]
    diagnostics: dict[str, Any] = field(default_factory=dict)
