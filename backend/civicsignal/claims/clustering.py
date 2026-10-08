"""
Claim clustering.

Comparable claims are grouped by (claim type, subject key, attribute). Inside
a cluster, claims are partitioned into value groups: claims whose value
intervals overlap (directly or through a chain) agree. A cluster with two or
more value groups contains conflicting claims.

Value groups are ordered by value, never by how many sources state them —
the number of sources repeating a value is not evidence that it is correct.
"""

from __future__ import annotations

import math

from ..models import ClaimCluster, ClaimType, ExtractedClaim, ValueGroup, new_id


def value_interval(claim: ExtractedClaim) -> tuple[float, float]:
    if claim.claim_type == ClaimType.DATE and claim.date:
        return float(claim.date.date_start.toordinal()), float(claim.date.date_end.toordinal())
    if claim.claim_type == ClaimType.AMOUNT and claim.amount:
        high = math.inf if claim.amount.high is None else claim.amount.high
        return claim.amount.low, high
    raise ValueError(f"claim {claim.id} has no comparable value")


def _ordered_unique(items):
    return list(dict.fromkeys(i for i in items if i))


def cluster_claims(claims: list[ExtractedClaim]) -> list[ClaimCluster]:
    buckets: dict[tuple, list[ExtractedClaim]] = {}
    for c in claims:
        if c.comparable:
            buckets.setdefault((c.claim_type, c.subject, c.attribute), []).append(c)

    clusters = []
    for (claim_type, subject, attribute), members in buckets.items():
        members = sorted(members, key=value_interval)
        groups: list[list[ExtractedClaim]] = []
        group_high = -math.inf
        for c in members:  # sweep: overlapping intervals chain into one group
            low, high = value_interval(c)
            if groups and low <= group_high:
                groups[-1].append(c)
                group_high = max(group_high, high)
            else:
                groups.append([c])
                group_high = high
        value_groups = [
            ValueGroup(
                value=" / ".join(_ordered_unique(c.value for c in g)),
                claim_ids=[c.id for c in g],
                source_ids=_ordered_unique(c.source_id for c in g),
            )
            for g in groups
        ]
        clusters.append(
            ClaimCluster(
                id=new_id("cls"),
                subject=subject,
                attribute=attribute,
                claim_type=claim_type,
                claim_ids=[c.id for c in members],
                source_ids=_ordered_unique(c.source_id for c in members),
                value_groups=value_groups,
                has_conflict=len(value_groups) > 1,
            )
        )
    return clusters
