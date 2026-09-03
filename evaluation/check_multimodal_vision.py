"""Exercise the production vision cascade on every benchmark figure block."""

from __future__ import annotations

import asyncio
import argparse
import json
import os
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "backend"))

import evaluation.run_multimodal_benchmark as benchmark
from app.services.visual_analysis import describe_figure_candidates


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("cascade", "groq"),
        default="cascade",
        help="Use the production cascade or isolate Groq for capacity diagnostics.",
    )
    return parser.parse_args()


async def main(provider: str) -> None:
    if provider == "groq":
        from app.config import settings

        settings.GEMINI_API_KEY = ""
        settings.GOOGLE_API_KEY = ""
        settings.OPENAI_API_KEY = ""
        settings.HF_TOKEN = ""
        settings.HUGGINGFACE_TOKEN = ""
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "HF_TOKEN", "HUGGINGFACE_TOKEN"):
            os.environ.pop(name, None)
    dataset = benchmark.load_dataset()
    chunks: list[dict] = []
    document_paths: dict[str, str] = {}
    for document in dataset["documents"]["evidence"]:
        path = benchmark.DOCS / document["filename"]
        chunks.extend(benchmark._ingest_document(path, document["doc_type"]))
        document_paths[path.stem] = str(path)

    figures = [
        chunk for chunk in chunks
        if (chunk.get("metadata") or {}).get("block_type") == "figure"
    ]
    stats = await describe_figure_candidates(
        [{"candidate_chunks": figures}],
        document_paths,
        model=benchmark.MULTIMODAL_DEFAULT_MODEL,
    )
    outcomes = []
    for figure in figures:
        visual = (figure.get("metadata") or {}).get("visual_analysis") or {}
        outcomes.append({
            "chunk_id": figure["id"],
            "document": figure["document_name"],
            "page": figure["page_number"],
            "status": visual.get("status"),
            "provider": visual.get("provider"),
            "model": visual.get("model"),
            "description_characters": len(str(visual.get("description") or "")),
        })
    print(json.dumps({"stats": stats, "figures": outcomes}, indent=2))


if __name__ == "__main__":
    arguments = parse_args()
    asyncio.run(main(arguments.provider))
