"""User visual profile — cluster liked/disliked covers into mean centroids.

Builds a multi-peak visual taste profile from the user's recommendation
feedback (like/dislike/save). Cover image vectors (same multimodal embedding
space) are greedily agglomerated into clusters; each cluster's centroid is the
*mean* of its member vectors (unlike ``discovery.engine._normalize_topic_keys``
which uses a canonical member as the label — here we want a numeric centroid
because consumers compare candidate cover vectors against it by cosine, and a
mean captures the shared visual style of a multi-member taste cluster).

Pure functions + a small dataclass. No I/O: the caller fetches feedback rows
and persists centroids. This keeps the clustering testable without a DB.

Why mean centroids, not a single global centroid: a user's taste is multi-peaked
— they may like both "dark game screenshots" and "bright food close-ups", which
a single mean would average into an incoherent grey. Keeping the top-k clusters
by membership preserves each peak; the consumer takes the *max* cosine against
all centroids (top-1 cluster), so a candidate matching any peak scores well.
"""

from __future__ import annotations

from dataclasses import dataclass

from openbiliclaw.llm.embedding import cosine_similarity

# Default cap on clusters kept per polarity (by membership, desc). A handful of
# peaks captures taste variety without flooding the hot path with cosine calls.
# CALIBRATION PROVENANCE: PROVISIONAL — not tuned against a real multimodal
# model's cover-vector distribution. Reopen after choosing a production model
# (CLAUDE.md pitfall rule 3).
DEFAULT_MAX_CLUSTERS = 5
# Minimum members for a cluster to survive (singletons are noise: one liked
# cover does not establish a taste peak).
DEFAULT_MIN_CLUSTER_MEMBERS = 2
# Cosine at/above which a new vector joins an existing cluster (same-modal
# image↔image). CALIBRATION PROVENANCE: MEASURED 2026-07-27 against dashscope
# qwen3-vl-embedding (dim=1024) — 452 real Bilibili covers, 101,926 pairs:
#   p50=0.219  p75=0.295  p90=0.365  p95=0.409  p99=0.497  p100=0.929
# The original 0.80 inherited the same "same-modal cosine runs high" intuition
# that made the cover/keyframe bonuses inert, and is provably unreachable
# here: p99 is 0.497, so 0.80 meant NO two real covers ever joined a cluster —
# every liked cover became a singleton and was pruned by min_members, leaving
# zero centroids and a silently no-op feature. 0.50 sits just above p99, so
# only genuinely visually-similar covers (the rare tail) merge into a taste
# peak; unrelated covers stay separate rather than polluting a centroid.
# Reopen after any embedding provider/model swap (CLAUDE.md pitfall rule 3):
# rerun scripts/calibrate_visual_thresholds.py --report and re-derive from the
# fresh p99.
DEFAULT_CLUSTER_THRESHOLD = 0.50


@dataclass(frozen=True)
class VisualCluster:
    """One cluster of liked (or disliked) cover vectors."""

    centroid: tuple[float, ...]
    member_count: int


def build_centroids(
    vectors: list[list[float]],
    *,
    threshold: float = DEFAULT_CLUSTER_THRESHOLD,
    max_clusters: int = DEFAULT_MAX_CLUSTERS,
    min_members: int = DEFAULT_MIN_CLUSTER_MEMBERS,
) -> list[VisualCluster]:
    """Greedy agglomerative cluster of cover vectors → mean centroids.

    Mirrors the loop shape of ``discovery.engine._normalize_topic_keys`` but
    keeps a running *mean* centroid per cluster (not a canonical member) and
    prunes small clusters + keeps only the top-``max_clusters`` by membership.

    Args:
        vectors: non-empty cover image vectors (same embedding space). Empty
            or all-zero vectors are skipped (a zero vector is a provider
            failure — caching/using it would poison the profile, per pitfall
            rule 2).
        threshold: cosine at/above which a vector joins the nearest cluster.
        max_clusters: keep at most this many clusters (by membership, desc).
        min_members: drop clusters with fewer members (singleton = noise).

    Returns:
        Centroids sorted by membership desc; empty if no usable vectors.
    """
    # Step 1: filter zero / empty vectors (never feed a failed embed into a
    # centroid — it would pull the mean toward the origin and corrupt cosine).
    usable: list[list[float]] = []
    for vec in vectors:
        if not vec or all(abs(x) < 1e-12 for x in vec):
            continue
        usable.append(list(vec))
    if not usable:
        return []

    # Step 2: greedy agglomerative clustering with a running mean centroid.
    centroids: list[list[float]] = []
    sums: list[list[float]] = []  # running sum per cluster for O(1) mean update
    counts: list[int] = []

    for vec in usable:
        best_idx: int | None = None
        best_sim = 0.0
        for idx, centroid in enumerate(centroids):
            sim = cosine_similarity(vec, centroid)
            if sim > best_sim:
                best_sim = sim
                best_idx = idx

        if best_idx is not None and best_sim >= threshold:
            for i, x in enumerate(vec):
                sums[best_idx][i] += x
            counts[best_idx] += 1
            centroids[best_idx] = [s / counts[best_idx] for s in sums[best_idx]]
        else:
            centroids.append(list(vec))
            sums.append(list(vec))
            counts.append(1)

    # Step 3: prune small clusters, keep top-k by membership.
    kept = [
        (counts[i], centroids[i])
        for i in range(len(centroids))
        if counts[i] >= min_members
    ]
    kept.sort(key=lambda item: item[0], reverse=True)
    kept = kept[:max(0, int(max_clusters))]

    return [
        VisualCluster(centroid=tuple(centroid), member_count=count)
        for count, centroid in kept
    ]


def best_centroid_similarity(
    candidate_vec: list[float],
    centroids: list[VisualCluster],
) -> float:
    """Max cosine of a candidate cover against the centroids (0 if none)."""
    if not candidate_vec or not centroids:
        return 0.0
    best = 0.0
    for cluster in centroids:
        sim = cosine_similarity(candidate_vec, list(cluster.centroid))
        if sim > best:
            best = sim
    return best


# --- Label-noise cleaning + contested-region detection -----------------------
#
# These operate on the raw liked/disliked cover vectors BEFORE clustering, and
# on the built centroids AFTER. They are the geometric fix for the neg-cancellation
# problem: liked and disliked covers overlap in visual space (binary feedback
# does not separate *visual* taste from topic/length/UP-style dislike), so a
# naive pos-minus-penalty cancels the signal. Instead of guessing thresholds,
# the geometry itself decides where the cover modality can speak.
#
# CALIBRATION PROVENANCE: MEASURED 2026-07-28 against dashscope
# qwen3-vl-embedding (dim=1024) on the live pool (scripts/measure_visual_profile_geometry.py):
#   - 2 pos + 3 neg centroids; 2/6 pos×neg pairs contested (pos0×neg0=0.674,
#     pos1×neg1=0.576) -> likes/dislikes are VISUALLY INTERLEAVED. The cover
#     modality cannot distinguish them in those regions.
#   - net (s_pos - s_neg) over 1366 candidate covers: p5=-0.154 p25=-0.075
#     p50=-0.024 p75=0.029 p90=0.078 p95=0.114. 61% of candidates are closer to
#     a disliked centroid than a liked one.
# Reopen after any embedding provider/model swap (rule 3): rerun the script.

# Cosine at/above which a pos/neg centroid pair is "contested" — the cover
# modality cannot distinguish like/dislike in this region (love-hate style).
# CALIBRATION: the 6 live pos×neg centroid pairs split into a high cluster
# (0.674, 0.576 — genuinely overlapping) and a low cluster (0.377, 0.327,
# 0.318, 0.219 — separated), with a clean gap between them. 0.45 sits in the
# gap: flags the 2 true overlaps, leaves the 4 separated pairs active.
# (0.40 was too low — centroid means are more cohesive than raw covers, so
# 0.40 tripped almost every pair and grayed the signal out entirely.)
DEFAULT_CONTESTED_THRESHOLD = 0.45

# |s_pos - s_neg| at/above which the cover modality has a clear opinion (boost
# if positive, suppress if negative); below it the candidate is gray (no say).
# From the live net distribution: p75=0.029, p90=0.078 -> 0.05 sits between them,
# so only the top ~20% of clearly-separated candidates earn a boost (and the
# bottom ~35% a suppress); the contested middle grays out. One number on the
# difference, not two floor/ceil on absolutes -> self-calibrating (the 0.80
# lesson generalized: s_pos and s_neg share one embedding/pipeline, so their
# difference needs no separate τ).
DEFAULT_MARGIN = 0.05


def _knn_mean(vec: list[float], others: list[list[float]], k: int) -> float:
    """Mean cosine to the k nearest vectors in ``others`` (0 if none)."""
    if not others or not vec:
        return 0.0
    sims = sorted((cosine_similarity(vec, o) for o in others), reverse=True)
    take = max(1, min(k, len(sims)))
    return sum(sims[:take]) / take


@dataclass(frozen=True)
class CrossCleanResult:
    """Outcome of label-noise removal before clustering."""

    kept_pos: list[list[float]]
    kept_neg: list[list[float]]
    dropped_pos: list[list[float]]
    dropped_neg: list[list[float]]


def cross_clean_labels(
    pos_vecs: list[list[float]],
    neg_vecs: list[list[float]],
    *,
    k: int = 3,
    drop_margin: float = 0.08,
) -> CrossCleanResult:
    """Drop feedback covers that sit in the ENEMY's territory (label noise).

    For each liked cover, compare its mean kNN-similarity to other liked covers
    vs to disliked covers. If the enemy side is closer by a clear ``drop_margin``
    (not a tie), the label is likely a misclick or a love-hate contradiction —
    drop it from centroid construction. Symmetric for disliked covers. Dropped
    vectors are returned (kept raw for later hard-negative use); they are NEVER
    flipped to the opposite polarity.

    Conservative by design: the ``drop_margin`` avoids pruning on near-ties,
    which matters on small feedback sets (a dozen likes) where one drop shifts
    the centroid a lot. Zero/empty vectors are skipped first (pitfall rule 2).

    Args:
        pos_vecs: liked cover vectors (same embedding space).
        neg_vecs: disliked cover vectors.
        k: kNN neighbors. Small (3) — feedback sets are small.
        drop_margin: enemy-similarity must exceed own-similarity by this much to
            drop. 0.08 keeps only clearer cases (live data: drops 2/12 pos at
            diff 0.055-0.058 are NOT dropped at 0.08; the clearer neg at 0.098
            is).

    Returns:
        Kept + dropped vectors per polarity.
    """
    pos = [v for v in pos_vecs if v and not all(abs(x) < 1e-12 for x in v)]
    neg = [v for v in neg_vecs if v and not all(abs(x) < 1e-12 for x in v)]

    def _split(group: list[list[float]], own: list[list[float]], opp: list[list[float]]):
        kept: list[list[float]] = []
        dropped: list[list[float]] = []
        for i, vec in enumerate(group):
            own_others = [own[j] for j in range(len(own)) if j != i]
            nn_own = _knn_mean(vec, own_others, k)
            nn_opp = _knn_mean(vec, opp, k)
            if nn_opp > nn_own + drop_margin:
                dropped.append(vec)
            else:
                kept.append(vec)
        return kept, dropped

    kept_pos, dropped_pos = _split(pos, pos, neg)
    kept_neg, dropped_neg = _split(neg, neg, pos)
    return CrossCleanResult(kept_pos, kept_neg, dropped_pos, dropped_neg)


def contested_pairs(
    pos_clusters: list[VisualCluster],
    neg_clusters: list[VisualCluster],
    *,
    threshold: float = DEFAULT_CONTESTED_THRESHOLD,
) -> set[tuple[int, int]]:
    """Pos/neg centroid index pairs whose cosine >= threshold (love-hate region).

    A contested pair marks a visual region where the cover modality cannot
    distinguish like/dislike — the user is ambivalent about that style. At
    scoring time, a candidate whose best-pos and best-neg centroids form a
    contested pair should abstain (gray), not boost-and-cancel.
    """
    pairs: set[tuple[int, int]] = set()
    for i, p in enumerate(pos_clusters):
        for j, n in enumerate(neg_clusters):
            if cosine_similarity(list(p.centroid), list(n.centroid)) >= threshold:
                pairs.add((i, j))
    return pairs
