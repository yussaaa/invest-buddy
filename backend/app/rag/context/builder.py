"""Context builder — assembles retrieved chunks into a formatted context string."""

from __future__ import annotations


def build_context(chunks: list[dict], max_tokens: int = 4000) -> str:
    """Assemble retrieved chunks into a context string for agent consumption.

    Sorts by freshness (newest first), formats each chunk with a source
    metadata header, and truncates to stay within the token budget.

    Args:
        chunks: List of retrieved chunk dicts (from pipeline.retrieve())
        max_tokens: Approximate max token budget (estimated at 4 chars/token)

    Returns:
        Formatted context string with source attribution per chunk.
    """
    if not chunks:
        return ""

    # Sort by freshness score (higher = newer = more relevant)
    sorted_chunks = sorted(
        chunks,
        key=lambda c: c.get("freshness_score") or 0.0,
        reverse=True,
    )

    max_chars = max_tokens * 4  # rough approximation
    lines: list[str] = []
    char_count = 0

    for i, chunk in enumerate(sorted_chunks, 1):
        # Build metadata header
        header_parts = [f"[Source {i}"]
        if chunk.get("document_type"):
            header_parts.append(f"Type: {chunk['document_type']}")
        if chunk.get("ticker"):
            header_parts.append(f"Ticker: {chunk['ticker']}")
        if chunk.get("published_date"):
            date_str = chunk["published_date"]
            if hasattr(date_str, "strftime"):
                date_str = date_str.strftime("%Y-%m-%d")
            header_parts.append(f"Date: {date_str}")
        if chunk.get("source_url"):
            header_parts.append(f"URL: {chunk['source_url']}")
        header = " | ".join(header_parts) + "]"

        text = chunk.get("text", "").strip()
        entry = f"{header}\n{text}\n"

        # Check budget
        if char_count + len(entry) > max_chars:
            # Try to fit a truncated version
            remaining = max_chars - char_count - len(header) - 20
            if remaining > 100:
                entry = f"{header}\n{text[:remaining]}...\n"
                lines.append(entry)
            break

        lines.append(entry)
        char_count += len(entry)

    return "\n---\n".join(lines)
