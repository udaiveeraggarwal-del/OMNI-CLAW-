"""Deterministic SEO helpers that operate only on user-supplied content.

These tools do not fetch URLs, publish changes, or claim to predict search
rankings. They provide a repeatable technical audit and metadata draft that a
human or higher-level agent can review before deployment.
"""

from __future__ import annotations

from html.parser import HTMLParser
import json
import re
from typing import Any, Dict, List, Optional

from omniagent.core.models import ToolResult
from omniagent.core.router import BaseTool
from omniagent.skills.base import BaseSkill


class _SEOHTMLParser(HTMLParser):
    """Small, non-networking HTML extractor for common on-page signals."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.description = ""
        self.canonical = ""
        self.headings: Dict[str, List[str]] = {f"h{i}": [] for i in range(1, 7)}
        self.images = 0
        self.images_without_alt = 0
        self._capture: Optional[str] = None
        self._capture_parts: List[str] = []
        self._ignore_depth = 0
        self._visible_parts: List[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"}:
            self._ignore_depth += 1
        if tag == "title":
            self._capture = "title"
            self._capture_parts = []
        elif tag in self.headings:
            self._capture = tag
            self._capture_parts = []
        elif tag == "meta" and values.get("name", "").lower() == "description":
            self.description = values.get("content", "").strip()
        elif tag == "link" and "canonical" in values.get("rel", "").lower().split():
            self.canonical = values.get("href", "").strip()
        elif tag == "img":
            self.images += 1
            if "alt" not in values:
                self.images_without_alt += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "template"} and self._ignore_depth:
            self._ignore_depth -= 1
        if self._capture == tag:
            text = " ".join(" ".join(self._capture_parts).split())
            if tag == "title":
                self.title = text
            elif tag in self.headings:
                self.headings[tag].append(text)
            self._capture = None
            self._capture_parts = []

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        if data.strip():
            self._visible_parts.append(data.strip())
        if self._capture:
            self._capture_parts.append(data)

    @property
    def visible_text(self) -> str:
        return " ".join(" ".join(self._visible_parts).split())


class AuditHTMLTool(BaseTool):
    @property
    def name(self) -> str:
        return "seo.audit_html"

    @property
    def description(self) -> str:
        return "Audit supplied HTML for common on-page SEO and accessibility signals; performs no network requests."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "html": {"type": "string", "minLength": 1, "maxLength": 2000000},
                "primary_keyword": {"type": "string", "maxLength": 200},
            },
            "required": ["html"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> ToolResult:
        html = kwargs.get("html")
        if not isinstance(html, str) or not html.strip():
            return ToolResult(success=False, output=None, error="html must be a non-empty string")
        if len(html) > 2_000_000:
            return ToolResult(success=False, output=None, error="html exceeds the 2 MB input limit")

        parser = _SEOHTMLParser()
        try:
            parser.feed(html)
            parser.close()
        except Exception as exc:
            return ToolResult(success=False, output=None, error=f"HTML parsing failed: {exc}")

        issues: List[Dict[str, str]] = []
        score = 100

        def issue(code: str, severity: str, message: str, penalty: int) -> None:
            nonlocal score
            score -= penalty
            issues.append({"code": code, "severity": severity, "recommendation": message})

        if not parser.title:
            issue("missing_title", "high", "Add a descriptive <title> element.", 10)
        elif not 30 <= len(parser.title) <= 60:
            issue("title_length", "low", "Review title length; 30–60 characters is a useful editorial target.", 4)
        if not parser.description:
            issue("missing_description", "high", "Add a concise meta description for the page.", 8)
        elif not 120 <= len(parser.description) <= 160:
            issue("description_length", "low", "Review description length; 120–160 characters is a useful editorial target.", 3)

        h1_count = len(parser.headings["h1"])
        if h1_count == 0:
            issue("missing_h1", "high", "Add one clear primary heading (H1).", 8)
        elif h1_count > 1:
            issue("multiple_h1", "medium", "Review the page structure and use one primary H1 where appropriate.", 5)
        if not parser.canonical:
            issue("missing_canonical", "medium", "Add a canonical link when this page may have duplicate URLs.", 4)
        if parser.images_without_alt:
            issue(
                "images_missing_alt",
                "medium",
                f"Add meaningful alt text to {parser.images_without_alt} image(s) that convey content.",
                min(10, parser.images_without_alt * 2),
            )

        keyword = " ".join(str(kwargs.get("primary_keyword", "")).split())
        keyword_report: Dict[str, Any] | None = None
        if keyword:
            keyword_re = re.compile(r"(?<!\w)" + re.escape(keyword) + r"(?!\w)", re.IGNORECASE)
            combined_headings = " ".join(text for values in parser.headings.values() for text in values)
            keyword_report = {
                "phrase": keyword,
                "count_in_text": len(keyword_re.findall(parser.visible_text)),
                "in_title": bool(keyword_re.search(parser.title)),
                "in_description": bool(keyword_re.search(parser.description)),
                "in_heading": bool(keyword_re.search(combined_headings)),
            }
            if not keyword_report["in_title"]:
                issue("keyword_not_in_title", "low", "If relevant to readers, consider including the primary phrase naturally in the title.", 3)
            if not keyword_report["in_heading"]:
                issue("keyword_not_in_heading", "low", "Consider whether a heading should clearly reflect the page topic.", 2)

        result = {
            "score": max(0, score),
            "score_note": "Heuristic checklist score only; it does not predict search ranking.",
            "issues": issues,
            "signals": {
                "title": parser.title,
                "title_characters": len(parser.title),
                "description": parser.description,
                "description_characters": len(parser.description),
                "canonical": parser.canonical or None,
                "heading_counts": {key: len(value) for key, value in parser.headings.items()},
                "images": parser.images,
                "images_without_alt": parser.images_without_alt,
                "visible_text_words": len(re.findall(r"\b[\w'-]+\b", parser.visible_text)),
                "keyword": keyword_report,
            },
        }
        return ToolResult(success=True, output=result)


class GenerateMetadataTool(BaseTool):
    @property
    def name(self) -> str:
        return "seo.generate_metadata"

    @property
    def description(self) -> str:
        return "Draft a meta description and WebPage JSON-LD from supplied page details without publishing them."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "page_title": {"type": "string", "minLength": 1, "maxLength": 300},
                "summary": {"type": "string", "minLength": 1, "maxLength": 10000},
                "primary_keyword": {"type": "string", "maxLength": 200},
                "language": {"type": "string", "maxLength": 35},
            },
            "required": ["page_title", "summary"],
            "additionalProperties": False,
        }

    @staticmethod
    def _truncate_at_word(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        prefix = text[:limit]
        clipped = prefix.rsplit(" ", 1)[0] if " " in prefix else prefix
        clipped = clipped.rstrip(" ,;:-")
        return clipped + "…"

    def execute(self, **kwargs: Any) -> ToolResult:
        title = " ".join(str(kwargs.get("page_title", "")).split())
        summary = " ".join(str(kwargs.get("summary", "")).split())
        keyword = " ".join(str(kwargs.get("primary_keyword", "")).split())
        language = str(kwargs.get("language", "en")).strip() or "en"
        if not title or not summary:
            return ToolResult(success=False, output=None, error="page_title and summary are required")

        source = summary
        if keyword and keyword.casefold() not in source.casefold():
            source = f"{keyword}: {source}"
        description = self._truncate_at_word(source, 160)
        metadata = {
            "title": self._truncate_at_word(title, 60),
            "meta_description": description,
            "json_ld": {
                "@context": "https://schema.org",
                "@type": "WebPage",
                "name": title,
                "description": description,
                "inLanguage": language,
            },
            "review_note": "Draft only. Verify accuracy, tone, and search snippet fit before publishing.",
        }
        json.dumps(metadata["json_ld"], ensure_ascii=False)
        return ToolResult(success=True, output=metadata)


class SeoOptimizerSkill(BaseSkill):
    @property
    def skill_id(self) -> str:
        return "seo"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Offline on-page SEO audits and reviewable metadata drafts."

    def get_tools(self) -> List[BaseTool]:
        return [AuditHTMLTool(), GenerateMetadataTool()]
