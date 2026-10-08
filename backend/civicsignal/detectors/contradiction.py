"""
Contradiction detector (within-source and cross-source).

Input: claim clusters (claims/clustering.py) — comparable claims grouped by
subject key + attribute, partitioned into value groups whose intervals do
not overlap.

  * Within-source contradiction: one source (canonical page) has claims in
    two or more value groups of the same cluster — e.g. its title says
    "Last date 30 June 2026" while its snippet says "deadline 31 July 2026".
    Reported once per source per cluster.
  * Cross-source conflict: the value groups of a cluster are stated by two
    or more different sources. Reported once per cluster, listing every
    value and every source/claim/evidence span behind it.

What is deliberately NOT a contradiction:
  * historical vs current dates ("the deadline was June 14, 2025" /
    "the deadline is June 14, 2026") — historical claims are never comparable
  * different subjects ("23rd installment" vs "24th installment",
    "children aged 5-17" vs the general deadline)
  * different attributes (deadline vs expected date; fee vs late fee)
  * values that overlap at their stated precision ("July 2026" vs "20 July 2026",
    "about ₹4,000" vs "₹4,200")

No value is ever declared correct. Value groups are listed in value order
with the number of sources stating each, and the explanation says
explicitly that more sources does not make a value right.
"""

from __future__ import annotations

from ..models import (
    ClaimCluster,
    ClaimType,
    ContradictionFinding,
    ExtractedClaim,
    FindingScope,
    Strength,
    ValueGroup,
    new_id,
)

DETECTOR_NAME = "contradiction"

_ATTRIBUTE_LABELS = {
    "deadline": "deadline",
    "expected_date": "expected date",
    "fee": "fee",
    "late_fee": "late fee",
    "annual_benefit": "annual benefit amount",
    "installment_amount": "installment amount",
    "coverage": "coverage amount",
}


def subject_label(subject: str) -> str:
    if not subject:
        return "the investigated topic"
    parts = []
    for q in subject.split("|"):
        kind, _, value = q.partition(":")
        parts.append(f"{kind} {value}" if kind in ("installment", "round") else value)
    return ", ".join(parts)


def _is_imprecise(claim: ExtractedClaim) -> bool:
    if claim.confidence < 1.0:
        return True
    return bool(claim.amount and claim.amount.qualifier)


def _describe_groups(groups: list[ValueGroup]) -> str:
    return "; ".join(
        f"{g.value} (stated by {len(g.source_ids)} source{'s' if len(g.source_ids) != 1 else ''})" for g in groups
    )


def _evidence_ids(claim_ids: list[str], by_id: dict[str, ExtractedClaim]) -> list[str]:
    return [sid for cid in claim_ids for sid in by_id[cid].evidence_span_ids]


def detect_contradictions(
    clusters: list[ClaimCluster], claims: list[ExtractedClaim]
) -> list[ContradictionFinding]:
    by_id = {c.id: c for c in claims}
    findings: list[ContradictionFinding] = []

    for cluster in clusters:
        if not cluster.has_conflict:
            continue
        attr = _ATTRIBUTE_LABELS.get(cluster.attribute, cluster.attribute)
        subj = subject_label(cluster.subject)
        is_date = cluster.claim_type == ClaimType.DATE
        hint = (
            "Different dates may reflect an extension or a later announcement. "
            "Check the official/primary source to see which value is current."
            if is_date
            else "Check the official/primary source for the current amount."
        )

        # --- within-source ---------------------------------------------------
        for source_id in cluster.source_ids:
            groups = [
                ValueGroup(
                    value=g.value,
                    claim_ids=[cid for cid in g.claim_ids if by_id[cid].source_id == source_id],
                    source_ids=[source_id],
                )
                for g in cluster.value_groups
            ]
            groups = [g for g in groups if g.claim_ids]
            if len(groups) < 2:
                continue
            claim_ids = [cid for g in groups for cid in g.claim_ids]
            involved = [by_id[cid] for cid in claim_ids]
            findings.append(
                ContradictionFinding(
                    id=new_id("fnd"),
                    detector=DETECTOR_NAME,
                    title="Internal contradiction",
                    explanation=(
                        f"The same source gives conflicting values for the {attr} of {subj}: "
                        f"{' vs '.join(g.value for g in groups)}. At most one of these can describe "
                        "the current state, and the source does not say which."
                    ),
                    strength=Strength.MEDIUM if any(_is_imprecise(c) for c in involved) else Strength.HIGH,
                    scope=FindingScope.WITHIN_SOURCE,
                    source_ids=[source_id],
                    result_ids=list(dict.fromkeys(c.result_id for c in involved)),
                    claim_ids=claim_ids,
                    evidence_span_ids=_evidence_ids(claim_ids, by_id),
                    cluster_id=cluster.id,
                    verification_hint=hint,
                    subject=cluster.subject,
                    attribute=cluster.attribute,
                    claim_a_id=groups[0].claim_ids[0],
                    claim_b_id=groups[1].claim_ids[0],
                    conflicting_values=[g.value for g in groups],
                    value_groups=groups,
                )
            )

        # --- cross-source ----------------------------------------------------
        if len(cluster.source_ids) < 2:
            continue
        groups = cluster.value_groups
        involved = [by_id[cid] for cid in cluster.claim_ids]
        findings.append(
            ContradictionFinding(
                id=new_id("fnd"),
                detector=DETECTOR_NAME,
                title="Conflicting claims across sources",
                explanation=(
                    f"Retrieved sources disagree on the {attr} of {subj}: {_describe_groups(groups)}. "
                    "Source counts are shown for context only — more sources repeating a value does not "
                    "make it correct, and repeated values may not be independent."
                ),
                strength=Strength.LOW if any(_is_imprecise(c) for c in involved) else Strength.MEDIUM,
                scope=FindingScope.CROSS_SOURCE,
                source_ids=cluster.source_ids,
                result_ids=list(dict.fromkeys(c.result_id for c in involved)),
                claim_ids=cluster.claim_ids,
                evidence_span_ids=_evidence_ids(cluster.claim_ids, by_id),
                cluster_id=cluster.id,
                verification_hint=hint,
                subject=cluster.subject,
                attribute=cluster.attribute,
                claim_a_id=groups[0].claim_ids[0],
                claim_b_id=groups[1].claim_ids[0],
                conflicting_values=[g.value for g in groups],
                value_groups=groups,
            )
        )
    return findings
