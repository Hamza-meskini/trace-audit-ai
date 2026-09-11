"""Live image-input smoke check using a generated, non-sensitive test image."""
import asyncio
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.llm_client import call_databricks_chat_completions


async def main():
    with pymupdf.open() as doc:
        page = doc.new_page(width=480, height=160)
        page.insert_text((30, 80), "VISION CHECK 7319", fontsize=30)
        png = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
    result = await call_databricks_chat_completions(
        "Transcribe the text in the image exactly. Return only that text.",
        model="system.ai.llama-4-maverick", image_bytes=png, max_output_tokens=128,
    )
    print("Image transcription:", result)
    return 0 if result and "7319" in result and "VISION" in result.upper() else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
