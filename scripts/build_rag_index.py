from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_OUTPUT = ROOT / "backend" / "data" / "rag_business_index.json"
CODE_OUTPUT = ROOT / "backend" / "data" / "rag_code_index.json"
BUSINESS_INPUTS = [
    ROOT / "README.md",
    ROOT / "docs",
]
CODE_INPUTS = [
    ROOT / "backend" / "app",
    ROOT / "frontend" / "src",
    ROOT / "scripts",
    ROOT / "backend" / "tests",
]
SUFFIXES = {".md", ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".css"}
EXCLUDED_PARTS = {".env", ".venv", "venv", "env", "node_modules", "__pycache__", "dist", ".runlogs", ".git"}
EXCLUDED_SUFFIXES = {".log", ".pyc", ".zip"}


def iter_files(inputs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        if item.is_file():
            if include_file(item):
                files.append(item)
        elif item.exists():
            files.extend(path for path in item.rglob("*") if include_file(path))
    return sorted(files)


def include_file(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in SUFFIXES or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return not any(part in EXCLUDED_PARTS or part.startswith(".env") for part in path.parts)


def chunk_text(path: Path, text: str, size: int = 1200, overlap: int = 160) -> list[dict[str, Any]]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    chunks = []
    start = 0
    index = 0
    while start < len(cleaned):
        part = cleaned[start : start + size].strip()
        if part:
            rel = path.relative_to(ROOT).as_posix()
            chunks.append(
                {
                    "id": hashlib.sha256(f"{rel}:{index}:{part[:80]}".encode("utf-8")).hexdigest()[:16],
                    "title": rel,
                    "text": part,
                    "tags": infer_tags(rel, part),
                }
            )
        index += 1
        start += max(1, size - overlap)
    return chunks


def infer_tags(path: str, text: str) -> list[str]:
    haystack = f"{path} {text}".lower()
    tags = []
    for tag in ("redis", "eventbus", "replay", "kpi", "chatbot", "affiliation", "sql", "architecture", "security", "dashboard"):
        if tag in haystack:
            tags.append(tag)
    return tags or ["project"]


def maybe_embed(chunks: list[dict[str, Any]]) -> str:
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore

        model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        vectors = model.encode([chunk["text"] for chunk in chunks], normalize_embeddings=True)
        for chunk, vector in zip(chunks, vectors, strict=False):
            chunk["embedding"] = [float(item) for item in vector]
        return "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    except Exception:
        return "lexical-fallback"


def write_index(output: Path, inputs: list[Path], kind: str) -> None:
    chunks: list[dict[str, Any]] = []
    for path in iter_files(inputs):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks.extend(chunk_text(path, text))
    embedding_model = maybe_embed(chunks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": kind,
                "embedding_model": embedding_model,
                "chunk_count": len(chunks),
                "chunks": chunks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"RAG {kind} index written to {output} ({len(chunks)} chunks, {embedding_model})")


def main() -> None:
    write_index(BUSINESS_OUTPUT, BUSINESS_INPUTS, "business")
    write_index(CODE_OUTPUT, CODE_INPUTS, "code")


if __name__ == "__main__":
    main()
