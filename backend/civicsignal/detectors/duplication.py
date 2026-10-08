"""
Source duplication / independence detector.

Question answered: how many apparently independent sources stand behind
what the results say? Result count, URL count, source count and domain count
are kept as they are; this adds relationship evidence on top.

Unit of comparison: Source (canonical page). Pages already merged by URL
canonicalization (tracking params, www/m., YouTube shorts vs watch) are one
source before this runs.

Text normalization: lower-case word tokens. A word is DISTINCTIVE if it is
alphabetic, 3+ letters, and not a stopword, a generic civic word ("last",
"date", "apply", "online", "scheme"...), a month, or one of the query's own
anchor words (every result shares those). A 3-word phrase ("shingle") counts
only if it contains at least one distinctive word — so generic wording and
shared claim values alone ("last date 14 june 2026") never create a relation.

Pairwise relations (thresholds fixed before evaluating on real data):
  NEAR_IDENTICAL       containment >= 0.80
  SUBSTANTIAL_OVERLAP  containment >= 0.50, or an identical run of >= 8 words
                       containing >= 3 distinctive words
  RELATED_TITLE        distinctive-title-word Jaccard >= 0.70 (>= 4 words each),
                       when the snippets do not establish more
  SAME_PUBLISHER       same registrable domain, except multi-tenant platforms
                       (YouTube, Facebook, Instagram, X, ...), where one domain
                       hosts unrelated authors
containment = shared counted shingles / counted shingles of the shorter text.
If either text has fewer than MIN_SHINGLES counted shingles, the snippet
comparison is "uncertain" and produces nothing.

Independence groups: union of sources linked by NEAR_IDENTICAL,
SUBSTANTIAL_OVERLAP or SAME_PUBLISHER. RELATED_TITLE is reported but does
not merge — a matching headline alone is weak evidence.

Findings (wording is deliberately hedged — similarity is not proof of copying):
  * one per textual relation: "Near-identical wording" / "High textual
    similarity" / "Closely related headlines"
  * "Repeated claim may not be independently confirmed" when a claim value is
    stated by N sources that form fewer than N independent groups
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import combinations

from ..claims.subjects import query_anchors
from ..models import (
    MERGING_RELATIONS,
    ClaimCluster,
    ClaimSupport,
    DuplicationFinding,
    EvidenceField,
    EvidenceSpan,
    ExtractedClaim,
    FindingScope,
    IndependenceSummary,
    RelationKind,
    SearchResult,
    Source,
    SourceGroup,
    SourceRelationship,
    Strength,
    new_id,
)

DETECTOR_NAME = "duplication"
TEXTUAL_RELATIONS = {RelationKind.NEAR_IDENTICAL, RelationKind.SUBSTANTIAL_OVERLAP}

NEAR_IDENTICAL_CONTAINMENT = 0.80
SUBSTANTIAL_CONTAINMENT = 0.50
LONG_RUN_WORDS = 8
LONG_RUN_DISTINCTIVE = 3
TITLE_JACCARD = 0.70
MIN_TITLE_WORDS = 4
MIN_SHINGLES = 6
SHINGLE_SIZE = 3
PHRASE_MIN_WORDS = 4
MAX_PHRASES = 5

# Multi-tenant platforms: one domain, many unrelated authors.
PLATFORM_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "instagram.com", "x.com", "twitter.com", "threads.net",
    "reddit.com", "quora.com", "medium.com", "linkedin.com", "t.me", "telegram.me", "sharechat.com",
    "blogspot.com", "wordpress.com", "pages.dev", "github.io", "substack.com", "dailyhunt.in",
}

_STOPWORDS = {
    "the", "a", "an", "of", "for", "in", "on", "to", "and", "or", "is", "are", "was", "were", "be", "been",
    "will", "with", "by", "from", "at", "as", "it", "its", "this", "that", "these", "those", "has", "have",
    "had", "can", "may", "not", "no", "but", "if", "than", "then", "their", "they", "you", "your", "our",
    "who", "what", "when", "how", "which", "all", "any", "also", "into", "more", "about", "after",
    "before", "until", "till", "now", "here", "there", "per", "via",
}
_GENERIC = {
    "date", "dates", "last", "next", "new", "latest", "free", "update", "updates", "updated", "deadline",
    "installment", "instalment", "installments", "apply", "application", "applications", "online",
    "status", "fee", "fees", "scheme", "schemes", "yojana", "government", "govt", "official", "website",
    "portal", "check", "details", "list", "form", "result", "results", "notification", "link", "linking",
    "process", "step", "steps", "guide", "know", "news", "today", "update", "released", "release",
    "india", "indian", "state", "central", "card", "registration", "eligibility", "documents", "amount",
    "payment", "complete", "full", "read", "click", "visit", "information",
}
_MONTHS = {
    "jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june", "jul",
    "july", "aug", "august", "sep", "sept", "september", "oct", "october", "nov", "november", "dec",
    "december",
}
_TOKEN = re.compile(r"[a-z0-9]+")
_TITLE_SUFFIX = re.compile(r"\s+[-|–—:]\s+(?=[^-|–—:]*$)")


@dataclass
class _Text:
    tokens: list[str]
    distinctive: list[bool]
    shingles: set[tuple[str, ...]]


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _prepare(text: str, anchors: set[str]) -> _Text:
    tokens = _tokens(text)
    distinctive = [
        t.isalpha() and len(t) >= 3 and t not in _STOPWORDS and t not in _GENERIC and t not in _MONTHS
        and t not in anchors
        for t in tokens
    ]
    shingles = {
        tuple(tokens[i : i + SHINGLE_SIZE])
        for i in range(len(tokens) - SHINGLE_SIZE + 1)
        if any(distinctive[i : i + SHINGLE_SIZE])
    }
    return _Text(tokens, distinctive, shingles)


def _title_words(title: str, anchors: set[str]) -> set[str]:
    parts = _TITLE_SUFFIX.split(title)
    if len(parts) > 1 and len(parts[-1].split()) <= 3:  # drop " - Site Name" style suffix
        title = parts[0]
    t = _prepare(title, anchors)
    return {w for w, d in zip(t.tokens, t.distinctive) if d}


def _shared_runs(a: _Text, b: _Text) -> tuple[int, list[str]]:
    """Longest identical word run (with >= LONG_RUN_DISTINCTIVE distinctive words), and shared phrases."""
    matcher = SequenceMatcher(None, a.tokens, b.tokens, autojunk=False)
    longest, phrases = 0, []
    for block in matcher.get_matching_blocks():
        if block.size < PHRASE_MIN_WORDS:
            continue
        n_distinctive = sum(a.distinctive[block.a : block.a + block.size])
        if n_distinctive == 0:
            continue
        phrases.append(" ".join(a.tokens[block.a : block.a + block.size]))
        if n_distinctive >= LONG_RUN_DISTINCTIVE:
            longest = max(longest, block.size)
    phrases.sort(key=len, reverse=True)
    return longest, phrases[:MAX_PHRASES]


def compare_texts(
    text_a: str, text_b: str, title_a: str, title_b: str, anchors: set[str]
) -> SourceRelationship | None:
    """Textual relation between two sources' texts, or None. (Source ids are filled by the caller.)"""
    a, b = _prepare(text_a, anchors), _prepare(text_b, anchors)
    containment = None
    longest, phrases = 0, []
    if min(len(a.shingles), len(b.shingles)) >= MIN_SHINGLES:
        containment = len(a.shingles & b.shingles) / min(len(a.shingles), len(b.shingles))
        longest, phrases = _shared_runs(a, b)

    title_sim = None
    ta, tb = _title_words(title_a, anchors), _title_words(title_b, anchors)
    if min(len(ta), len(tb)) >= MIN_TITLE_WORDS:
        title_sim = len(ta & tb) / len(ta | tb)

    kind = None
    if containment is not None and containment >= NEAR_IDENTICAL_CONTAINMENT:
        kind = RelationKind.NEAR_IDENTICAL
    elif (containment is not None and containment >= SUBSTANTIAL_CONTAINMENT) or longest >= LONG_RUN_WORDS:
        kind = RelationKind.SUBSTANTIAL_OVERLAP
    elif title_sim is not None and title_sim >= TITLE_JACCARD:
        kind = RelationKind.RELATED_TITLE
    if kind is None:
        return None
    return SourceRelationship(
        source_a_id="",
        source_b_id="",
        kind=kind,
        text_containment=None if containment is None else round(containment, 3),
        longest_shared_run=longest,
        title_similarity=None if title_sim is None else round(title_sim, 3),
        shared_phrases=phrases,
        merges_independence=kind in MERGING_RELATIONS,
    )


# --- union-find ---------------------------------------------------------------------


class _Groups:
    def __init__(self, ids: list[str]):
        self.parent = {i: i for i in ids}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


# --- detector -------------------------------------------------------------------------


@dataclass
class DuplicationOutput:
    summary: IndependenceSummary
    findings: list[DuplicationFinding]
    evidence_spans: list[EvidenceSpan]
    duplicate_groups: int
    independent_groups: int


_TITLES = {
    RelationKind.NEAR_IDENTICAL: ("Near-identical wording", Strength.HIGH),
    RelationKind.SUBSTANTIAL_OVERLAP: ("High textual similarity", Strength.MEDIUM),
    RelationKind.RELATED_TITLE: ("Closely related headlines", Strength.LOW),
}


def _explain(rel: SourceRelationship, differing: list[str]) -> tuple[str, str]:
    if rel.kind == RelationKind.RELATED_TITLE:
        text = (
            f"These results have closely related headlines (title word overlap {rel.title_similarity:.0%}). "
            "They may report the same underlying story, for example the same wire report; the snippets "
            "alone are not enough to establish whether the content is shared."
        )
        method = "Jaccard similarity of distinctive headline words"
    else:
        parts = []
        if rel.text_containment is not None:
            parts.append(f"{rel.text_containment:.0%} of the shorter text's distinctive 3-word phrases also appear in the other")
        if rel.longest_shared_run >= LONG_RUN_WORDS:
            parts.append(f"they share an identical {rel.longest_shared_run}-word passage")
        quality = "near-identical wording" if rel.kind == RelationKind.NEAR_IDENTICAL else "highly similar wording"
        text = (
            f"These results contain {quality} ({'; '.join(parts)}). This is possible syndicated or repeated "
            "content, so they may not represent independent confirmation. Similar wording alone does not "
            "establish who published first or whether one copied the other."
        )
        method = "3-word phrase containment over distinctive wording; longest identical passage"
    if rel.shared_phrases:
        text += " Shared phrases: " + "; ".join(f'"{p}"' for p in rel.shared_phrases[:3]) + "."
    if differing:
        text += (
            " Note: despite the similar wording, the stated values differ ("
            + "; ".join(differing)
            + "), which may indicate a reused template or an updated copy."
        )
    return text, method


def _differing_values(
    a: str, b: str, clusters: list[ClaimCluster], claims_by_id: dict[str, ExtractedClaim]
) -> list[str]:
    out = []
    for cluster in clusters:
        groups_a = {i for i, g in enumerate(cluster.value_groups) if a in g.source_ids}
        groups_b = {i for i, g in enumerate(cluster.value_groups) if b in g.source_ids}
        if groups_a and groups_b and not (groups_a & groups_b):
            va = " / ".join(cluster.value_groups[i].value for i in sorted(groups_a))
            vb = " / ".join(cluster.value_groups[i].value for i in sorted(groups_b))
            out.append(f"{cluster.attribute}: {va} vs {vb}")
    return out


def _source_text(source: Source, results_by_id: dict[str, SearchResult]) -> tuple[str, str, list[SearchResult]]:
    results = [results_by_id[rid] for rid in source.result_ids]
    snippets = list(dict.fromkeys(r.snippet for r in results if r.snippet))
    titles = list(dict.fromkeys(r.title for r in results if r.title))
    return " | ".join(snippets), (titles[0] if titles else ""), results


def detect_duplication(
    results: list[SearchResult],
    sources: list[Source],
    claims: list[ExtractedClaim],
    clusters: list[ClaimCluster],
    topic: str,
) -> DuplicationOutput:
    anchors = query_anchors(topic)
    results_by_id = {r.id: r for r in results}
    claims_by_id = {c.id: c for c in claims}
    texts = {s.id: _source_text(s, results_by_id) for s in sources}

    relationships: list[SourceRelationship] = []
    for sa, sb in combinations(sources, 2):
        rel = compare_texts(texts[sa.id][0], texts[sb.id][0], texts[sa.id][1], texts[sb.id][1], anchors)
        if rel is None and sa.registrable_domain == sb.registrable_domain and sa.registrable_domain not in PLATFORM_DOMAINS:
            rel = SourceRelationship(
                source_a_id="", source_b_id="", kind=RelationKind.SAME_PUBLISHER,
                shared_domain=sa.registrable_domain, merges_independence=True,
            )
        elif rel is not None and sa.registrable_domain == sb.registrable_domain:
            rel.shared_domain = sa.registrable_domain
        if rel is not None:
            rel.source_a_id, rel.source_b_id = sa.id, sb.id
            relationships.append(rel)

    # independence groups
    uf = _Groups([s.id for s in sources])
    for rel in relationships:
        if rel.merges_independence:
            uf.union(rel.source_a_id, rel.source_b_id)
    members: dict[str, list[str]] = {}
    for s in sources:
        members.setdefault(uf.find(s.id), []).append(s.id)
    groups = []
    for ids in members.values():
        kinds = sorted(
            {r.kind for r in relationships if r.merges_independence and r.source_a_id in ids},
            key=lambda k: k.value,
        )
        groups.append(SourceGroup(id=new_id("grp"), source_ids=ids, relation_kinds=kinds))
    group_of = {sid: g.id for g in groups for sid in g.source_ids}
    summary = IndependenceSummary(relationships=relationships, groups=groups)

    findings: list[DuplicationFinding] = []
    spans: list[EvidenceSpan] = []

    def source_spans(source_id: str) -> list[str]:
        ids = []
        for r in texts[source_id][2]:
            for field, value in ((EvidenceField.TITLE, r.title), (EvidenceField.SNIPPET, r.snippet)):
                if value:
                    span = EvidenceSpan(
                        id=new_id("ev"), result_id=r.id, source_id=source_id, field=field,
                        text=value, start=0, end=len(value),
                    )
                    spans.append(span)
                    ids.append(span.id)
        return ids

    # pairwise textual findings
    for rel in relationships:
        if rel.kind == RelationKind.SAME_PUBLISHER:
            continue
        differing = _differing_values(rel.source_a_id, rel.source_b_id, clusters, claims_by_id)
        explanation, method = _explain(rel, differing)
        title, strength = _TITLES[rel.kind]
        pair = [rel.source_a_id, rel.source_b_id]
        findings.append(
            DuplicationFinding(
                id=new_id("fnd"),
                detector=DETECTOR_NAME,
                title=title,
                explanation=explanation,
                strength=strength,
                scope=FindingScope.CROSS_SOURCE,
                source_ids=pair,
                result_ids=[r.id for sid in pair for r in texts[sid][2]],
                evidence_span_ids=source_spans(rel.source_a_id) + source_spans(rel.source_b_id),
                verification_hint="Treat closely related results as one line of evidence, not several confirmations.",
                relation=rel.kind.value,
                similarity=rel.text_containment if rel.text_containment is not None else (rel.title_similarity or 0.0),
                method=method,
                text_containment=rel.text_containment,
                title_similarity=rel.title_similarity,
                longest_shared_run=rel.longest_shared_run,
                shared_phrases=rel.shared_phrases,
                differing_values=differing,
            )
        )

    # claim support: does apparent agreement come from independent groups?
    support: list[ClaimSupport] = []
    for cluster in clusters:
        for vg in cluster.value_groups:
            n_groups = len({group_of[sid] for sid in vg.source_ids})
            support.append(
                ClaimSupport(cluster_id=cluster.id, value=vg.value, source_ids=vg.source_ids, independent_groups=n_groups)
            )
            if len(vg.source_ids) < 2 or n_groups == len(vg.source_ids):
                continue
            linking = [
                r for r in relationships
                if r.merges_independence and r.source_a_id in vg.source_ids and r.source_b_id in vg.source_ids
            ]
            textual_link = any(r.kind in TEXTUAL_RELATIONS for r in linking)
            reasons = sorted({r.kind.value.replace("_", " ") for r in linking})
            claim_ids = [cid for cid in vg.claim_ids if claims_by_id[cid].source_id in vg.source_ids]
            findings.append(
                DuplicationFinding(
                    id=new_id("fnd"),
                    detector=DETECTOR_NAME,
                    title="Repeated claim may not be independently confirmed",
                    explanation=(
                        f"{len(vg.source_ids)} sources state {vg.value} for this {cluster.attribute.replace('_', ' ')}, "
                        f"but they form only {n_groups} apparently independent group(s) "
                        f"(related by: {', '.join(reasons)}). Agreement among related sources is weaker "
                        "evidence than agreement among independent ones."
                    ),
                    strength=Strength.MEDIUM if textual_link else Strength.LOW,
                    scope=FindingScope.CROSS_SOURCE,
                    source_ids=vg.source_ids,
                    result_ids=list(dict.fromkeys(claims_by_id[c].result_id for c in claim_ids)),
                    claim_ids=claim_ids,
                    evidence_span_ids=[sid for c in claim_ids for sid in claims_by_id[c].evidence_span_ids],
                    cluster_id=cluster.id,
                    verification_hint="Look for a primary source that states this value directly.",
                    relation="repeated_claim",
                    similarity=round(n_groups / len(vg.source_ids), 3),
                    method="independence groups among the sources stating this value",
                    supporting_sources=len(vg.source_ids),
                    independent_groups=n_groups,
                )
            )

    summary.claim_support = support
    return DuplicationOutput(
        summary=summary,
        findings=findings,
        evidence_spans=spans,
        duplicate_groups=duplicate_group_count(summary),
        independent_groups=len(groups),
    )


def duplicate_group_count(summary: IndependenceSummary) -> int:
    """Groups of 2+ sources linked by shared wording (same-publisher-only groups excluded)."""
    return sum(1 for g in summary.groups if len(g.source_ids) > 1 and TEXTUAL_RELATIONS & set(g.relation_kinds))
