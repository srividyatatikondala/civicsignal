"""
Turn a raw SerpApi response into a flat list of result items.

Every SerpApi field is treated as optional. Each item keeps its original
raw dict so nothing is lost. Supported blocks:
  * organic_results             (engine=google)
  * answer_box                  (engine=google)
  * related_questions           (engine=google, "People also ask")
  * top_stories                 (engine=google)
  * news_results (+ stories)    (engine=google_news)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import ResultType


@dataclass
class ParsedItem:
    result_type: ResultType
    position: int | None
    title: str
    link: str | None
    displayed_link: str | None
    snippet: str
    date: str | None
    raw: dict[str, Any] = field(default_factory=dict)


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _opt_str(value: Any) -> str | None:
    s = _str(value)
    return s or None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list_of_dicts(value: Any) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _organic(raw: dict) -> list[ParsedItem]:
    items = []
    for r in _list_of_dicts(raw.get("organic_results")):
        items.append(
            ParsedItem(
                result_type=ResultType.ORGANIC,
                position=_int(r.get("position")),
                title=_str(r.get("title")),
                link=_opt_str(r.get("link")),
                displayed_link=_opt_str(r.get("displayed_link")),
                snippet=_str(r.get("snippet")),
                date=_opt_str(r.get("date")),
                raw=r,
            )
        )
    return items


def _answer_box(raw: dict) -> list[ParsedItem]:
    box = raw.get("answer_box")
    if not isinstance(box, dict):
        return []
    snippet = _str(box.get("snippet")) or _str(box.get("answer")) or _str(box.get("result"))
    highlighted = box.get("snippet_highlighted_words")
    if not snippet and isinstance(highlighted, list):
        snippet = " ".join(str(w) for w in highlighted)
    return [
        ParsedItem(
            result_type=ResultType.ANSWER_BOX,
            position=0,
            title=_str(box.get("title")),
            link=_opt_str(box.get("link")),
            displayed_link=_opt_str(box.get("displayed_link")),
            snippet=snippet,
            date=_opt_str(box.get("date")),
            raw=box,
        )
    ]


def _related_questions(raw: dict) -> list[ParsedItem]:
    items = []
    for i, r in enumerate(_list_of_dicts(raw.get("related_questions")), start=1):
        items.append(
            ParsedItem(
                result_type=ResultType.RELATED_QUESTION,
                position=i,
                title=_str(r.get("question")) or _str(r.get("title")),
                link=_opt_str(r.get("link")),
                displayed_link=_opt_str(r.get("displayed_link")),
                snippet=_str(r.get("snippet")),
                date=_opt_str(r.get("date")),
                raw=r,
            )
        )
    return items


def _top_stories(raw: dict) -> list[ParsedItem]:
    items = []
    for i, r in enumerate(_list_of_dicts(raw.get("top_stories")), start=1):
        items.append(
            ParsedItem(
                result_type=ResultType.TOP_STORY,
                position=i,
                title=_str(r.get("title")),
                link=_opt_str(r.get("link")),
                displayed_link=_opt_str(r.get("source")),
                snippet=_str(r.get("snippet")),
                date=_opt_str(r.get("date")),
                raw=r,
            )
        )
    return items


def _news(raw: dict) -> list[ParsedItem]:
    items = []
    position = 0
    for r in _list_of_dicts(raw.get("news_results")):
        # google_news may nest a cluster of stories under one headline
        entries = _list_of_dicts(r.get("stories")) or [r]
        for s in entries:
            position += 1
            source = s.get("source")
            source_name = source.get("name") if isinstance(source, dict) else source
            items.append(
                ParsedItem(
                    result_type=ResultType.NEWS,
                    position=_int(s.get("position")) or position,
                    title=_str(s.get("title")),
                    link=_opt_str(s.get("link")),
                    displayed_link=_opt_str(source_name),
                    snippet=_str(s.get("snippet")),
                    date=_opt_str(s.get("date")),
                    raw=s,
                )
            )
    return items


def parse_response(raw: dict[str, Any], engine: str) -> list[ParsedItem]:
    if not isinstance(raw, dict):
        return []
    if engine == "google_news":
        return _news(raw)
    return _answer_box(raw) + _organic(raw) + _top_stories(raw) + _related_questions(raw)
