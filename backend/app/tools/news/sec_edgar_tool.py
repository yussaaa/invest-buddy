"""SEC EDGAR filing tool — downloads 10-K, 10-Q, 8-K filings.

Uses the sec-edgar-downloader library which wraps the public SEC EDGAR API
(no API key needed). Returns filing metadata + first 2000 chars of content
for the agent context window.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Literal

import structlog

from app.tools.retry_decorator import retry_network_errors

log = structlog.get_logger(__name__)


async def get_sec_filings(
    ticker: str,
    filing_type: Literal["10-K", "10-Q", "8-K"] = "10-K",
    limit: int = 3,
) -> dict:
    """Download and return recent SEC filings metadata + excerpt."""

    @retry_network_errors
    def _fetch():
        from sec_edgar_downloader import Downloader

        with tempfile.TemporaryDirectory() as tmpdir:
            dl = Downloader("AgentInvest", "agent@invest.example.com", tmpdir)
            dl.get(filing_type, ticker, limit=limit)

            base = Path(tmpdir) / "sec-edgar-filings" / ticker.upper() / filing_type
            filings = []

            if not base.exists():
                return {
                    "ticker": ticker.upper(),
                    "filing_type": filing_type,
                    "filings": [],
                    "caveat": "No filings found.",
                }

            for filing_dir in sorted(base.iterdir())[:limit]:
                txt_files = list(filing_dir.glob("*.txt")) + list(filing_dir.glob("*.htm"))
                if not txt_files:
                    continue

                primary = txt_files[0]
                try:
                    content = primary.read_text(encoding="utf-8", errors="ignore")
                    excerpt = content[:2000].strip()
                except Exception:
                    excerpt = ""

                filings.append({
                    "filing_type": filing_type,
                    "ticker": ticker.upper(),
                    "accession_number": filing_dir.name,
                    "file_path": str(primary),
                    "excerpt": excerpt,
                    "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}&type={filing_type}",
                })

            return {
                "ticker": ticker.upper(),
                "filing_type": filing_type,
                "filings_found": len(filings),
                "filings": filings,
            }

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as e:
        log.error("sec_edgar_error", ticker=ticker, filing_type=filing_type, error=str(e))
        return {
            "ticker": ticker.upper(),
            "filing_type": filing_type,
            "filings": [],
            "error": str(e),
        }
