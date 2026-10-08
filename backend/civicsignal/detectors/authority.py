"""
Source type / authority analyzer and primary-source support.

Source type is CONTEXT for evidence, never a verdict: an official page can be
outdated and a news page can be right. No scores, rankings or good/bad labels.

Classification (first match wins; every label records the signal behind it):
  OFFICIAL          host ends in .gov.in / .nic.in / .gov / .gov.xx / .mil(.xx),
                    or an Indian academic/research institution (.ac.in, .edu.in,
                    .res.in, .edu), or a short documented list of official bodies
                    that use other domains (e.g. rbi.org.in)
  USER_GENERATED    video / social / forum platforms
  ESTABLISHED_NEWS  documented list of established news organisations
  TERTIARY          self-publishing hosts, aggregators, finance/exam explainer
                    portals (documented list), or a domain name containing
                    jobs/results-portal words ("sarkari", "naukri", "job", "result", "yojana")
  UNKNOWN           everything else — not reliably classifiable

Primary-source support: a source is a primary-source candidate only if it is
OFFICIAL *and* at least one of its results is topic-relevant (relevance HIGH
or MEDIUM). Findings (thresholds fixed in advance):
  0 candidates                      -> "Limited primary-source coverage" (MEDIUM)
  candidates < 25% of sources       -> "Few primary sources among results" (LOW)
For every comparable claim value, the official sources stating it are listed
(value_support), so the report can show which values have official backing
among the retrieved results.
"""

from __future__ import annotations

import re
from collections import Counter

from ..claims.subjects import mentions_anchor, query_anchors
from ..models import (
    AuthorityFinding,
    ClaimCluster,
    ExtractedClaim,
    FindingScope,
    RelevanceLevel,
    SearchResult,
    Source,
    SourceType,
    Strength,
    ValueOfficialSupport,
    new_id,
)

DETECTOR_NAME = "authority"
LOW_PRIMARY_SHARE = 0.25

_OFFICIAL_HOST = [
    (re.compile(r"(^|\.)(gov|nic)\.in$"), "government domain (.gov.in / .nic.in)"),
    (re.compile(r"(^|\.)gov(\.[a-z]{2})?$"), "government domain (.gov)"),
    (re.compile(r"(^|\.)mil(\.[a-z]{2})?$"), "government domain (.mil)"),
    (re.compile(r"(^|\.)(ac|edu|res)\.in$"), "Indian academic/research institution domain"),
    (re.compile(r"(^|\.)edu$"), "academic institution domain (.edu)"),
]
# Official bodies that do not use .gov.in (kept short and documented).
OFFICIAL_DOMAINS = {"rbi.org.in", "npci.org.in", "irctc.co.in", "uidai.gov.in"}

USER_GENERATED_DOMAINS = {
    "youtube.com", "youtu.be", "facebook.com", "instagram.com", "x.com", "twitter.com", "threads.net",
    "reddit.com", "quora.com", "linkedin.com", "t.me", "telegram.me", "sharechat.com", "whatsapp.com",
}
ESTABLISHED_NEWS_DOMAINS = {
    "thehindu.com", "indianexpress.com", "newindianexpress.com", "hindustantimes.com", "livemint.com",
    "indiatimes.com", "ndtv.com", "news18.com", "moneycontrol.com", "business-standard.com",
    "financialexpress.com", "deccanherald.com", "deccanchronicle.com", "thehansindia.com",
    "telanganatoday.com", "scroll.in", "theprint.in", "thewire.in", "indiatoday.in", "aajtak.in",
    "abplive.com", "timesnownews.com", "etnownews.com", "firstpost.com", "tribuneindia.com",
    "telegraphindia.com", "outlookindia.com", "freepressjournal.in", "morungexpress.com", "newsmeter.in",
    "thequint.com", "dnaindia.com", "republicworld.com", "mathrubhumi.com", "manoramaonline.com",
    "sakshi.com", "eenadu.net", "lokmat.com", "bhaskar.com", "amarujala.com", "jagran.com",
    "livehindustan.com", "oneindia.com", "bbc.com", "bbc.co.uk", "reuters.com", "apnews.com",
}
# Hosts (not registrable domains) for news brands that live on a shared portal domain.
ESTABLISHED_NEWS_HOSTS = {"zeenews.india.com"}
TERTIARY_DOMAINS = {
    # self-publishing hosts and aggregators
    "blogspot.com", "wordpress.com", "medium.com", "substack.com", "pages.dev", "github.io", "wixsite.com",
    "dailyhunt.in",
    # finance / exam / services explainer portals
    "bajajfinserv.in", "paisabazaar.com", "policybazaar.com", "bankbazaar.com", "cleartax.in",
    "razorpay.com", "iifl.com", "groww.in", "paytm.com", "testbook.com", "careers360.com", "shiksha.com",
    "jagranjosh.com", "tender247.com",
}
_PORTAL_WORDS = re.compile(r"sarkari|naukri|govtjob|job|result|yojana", re.IGNORECASE)


def classify_source(host: str, registrable: str) -> tuple[SourceType, list[str]]:
    host = (host or "").lower()
    registrable = (registrable or "").lower()
    for pattern, reason in _OFFICIAL_HOST:
        if pattern.search(host):
            return SourceType.OFFICIAL, [reason]
    if host in OFFICIAL_DOMAINS or registrable in OFFICIAL_DOMAINS:
        return SourceType.OFFICIAL, ["listed official body"]
    if registrable in USER_GENERATED_DOMAINS:
        return SourceType.USER_GENERATED, [f"video/social platform ({registrable})"]
    if host in ESTABLISHED_NEWS_HOSTS or registrable in ESTABLISHED_NEWS_DOMAINS:
        return SourceType.ESTABLISHED_NEWS, ["listed established news organisation"]
    if registrable in TERTIARY_DOMAINS:
        return SourceType.TERTIARY, ["listed blog host / aggregator / explainer portal"]
    label = registrable.split(".")[0]
    if _PORTAL_WORDS.search(label):
        return SourceType.TERTIARY, [f"domain name suggests a jobs/results/scheme portal ({registrable})"]
    return SourceType.UNKNOWN, ["no reliable classification signal"]


def _topic_relevant(source: Source, results_by_id: dict[str, SearchResult], anchors: set[str]) -> bool:
    results = [results_by_id[rid] for rid in source.result_ids]
    if any(r.relevance is not None for r in results):
        return any(r.relevance in (RelevanceLevel.HIGH, RelevanceLevel.MEDIUM) for r in results)
    # relevance analyzer unavailable: fall back to an anchor mention
    return any(mentions_anchor(f"{r.title} {r.snippet}", anchors) for r in results)


def analyze_authority(
    results: list[SearchResult],
    sources: list[Source],
    claims: list[ExtractedClaim],
    clusters: list[ClaimCluster],
    query: str,
) -> tuple[list[AuthorityFinding], list[ValueOfficialSupport], dict[str, int], int]:
    """Returns (findings, value_support, type_counts, primary_source_count). Sets type on every source."""
    for s in sources:
        s.source_type, s.type_signals = classify_source(s.domain, s.registrable_domain)
    results_by_id = {r.id: r for r in results}
    anchors = query_anchors(query)
    official = {s.id for s in sources if s.source_type == SourceType.OFFICIAL}
    primary = [s for s in sources if s.id in official and _topic_relevant(s, results_by_id, anchors)]
    type_counts = dict(Counter(s.source_type.value for s in sources))

    value_support = [
        ValueOfficialSupport(
            cluster_id=c.id,
            attribute=c.attribute,
            value=g.value,
            source_ids=g.source_ids,
            official_source_ids=[sid for sid in g.source_ids if sid in official],
        )
        for c in clusters
        for g in c.value_groups
    ]

    findings: list[AuthorityFinding] = []
    total = len(sources)
    if total:
        share = len(primary) / total
        unbacked = [v for v in value_support if not v.official_source_ids]
        note = ""
        if unbacked:
            note = (
                " Claim values with no official source among the results: "
                + "; ".join(f"{v.attribute.replace('_', ' ')} {v.value}" for v in unbacked[:5])
                + "."
            )
        if not primary:
            other_official = len(official)
            title, strength = "Limited primary-source coverage", Strength.MEDIUM
            explanation = (
                f"None of the {total} retrieved sources is an official/government page that addresses this "
                "topic"
                + (f" ({other_official} official page(s) appeared but do not address it)" if other_official else "")
                + ". Check the information in these results against the primary source." + note
            )
        elif share < LOW_PRIMARY_SHARE:
            title, strength = "Few primary sources among results", Strength.LOW
            explanation = (
                f"Only {len(primary)} of {total} retrieved sources are official pages that address this topic; "
                "most of the inspected information comes from secondary or unclassified sources." + note
            )
        else:
            title = None
        if title:
            findings.append(
                AuthorityFinding(
                    id=new_id("fnd"),
                    detector=DETECTOR_NAME,
                    title=title,
                    explanation=explanation,
                    strength=strength,
                    scope=FindingScope.LANDSCAPE,
                    source_ids=[s.id for s in primary],
                    verification_hint="Check this claim against the primary (official) source.",
                    primary_sources=len(primary),
                    total_sources=total,
                    type_counts=type_counts,
                    value_support=value_support,
                )
            )
    return findings, value_support, type_counts, len(primary)
