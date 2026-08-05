"""Markdown bold formatting for ATS preview output."""

from __future__ import annotations

import re

from app.services.keyword_utils import normalize_keyword


def boldImportantKeywords(generated_resume: str, important_keywords: list[str]) -> str:
    """Bold important technologies with Markdown **term** without altering protected identity lines."""
    if not generated_resume:
        return ""
    lines = generated_resume.split("\n")
    # Assume first contact block ends at first blank line or known section heading
    keywords = sorted(
        {normalize_keyword(k) for k in important_keywords if k},
        key=len,
        reverse=True,
    )
    if not keywords:
        return generated_resume

    def bold_line(line: str) -> str:
        # Skip likely contact / date-only lines
        if re.search(r"@|https?://|linkedin\.com|github\.com", line, re.I):
            return line
        if re.fullmatch(r"[\d\s\-–—/A-Za-z.,]+", line) and re.search(r"\d{4}", line) and len(line) < 40:
            return line
        result = line
        for keyword in keywords:
            pattern = re.compile(
                rf"(?<!\*)(?<![A-Za-z0-9])({re.escape(keyword)})(?![A-Za-z0-9])(?!\*)",
                re.I,
            )

            def repl(match):
                return f"**{match.group(1)}**"

            result = pattern.sub(repl, result)
        return result

    return "\n".join(bold_line(line) for line in lines)


# snake_case alias for Python style callers
def bold_important_keywords(generated_resume: str, important_keywords: list[str]) -> str:
    return boldImportantKeywords(generated_resume, important_keywords)
