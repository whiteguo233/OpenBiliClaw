"""Recommendation-output exclusion policy regressions."""

from openbiliclaw.recommendation.exclusion import (
    dislike_term_scope,
    disliked_topics_digest,
    filter_recommendation_rows,
    phrase_dislike_terms,
    recommendation_row_matches,
    recommendation_row_matches_exact,
)


def test_filters_exact_structured_topic_and_keeps_unrelated_rows() -> None:
    rows = [
        {"id": 1, "title": "办公室拉伸", "topic_group": "运动康复"},
        {"id": 2, "title": "SQLite 查询优化", "topic_group": "数据库"},
    ]

    result = filter_recommendation_rows(rows, ["运动康复"])

    assert [row["id"] for row in result] == [2]


def test_filters_fuzzy_match_when_safe_alternative_exists() -> None:
    rows = [
        {"id": 1, "title": "运动康复的居家训练指南"},
        {"id": 2, "title": "从零搭建个人知识库"},
    ]

    result = filter_recommendation_rows(rows, ["运动康复"])

    assert [row["id"] for row in result] == [2]


def test_total_fuzzy_wipeout_restores_exact_safe_rows_to_avoid_false_positive_starvation() -> None:
    rows = [
        {"id": 1, "title": "视频里的数据库原理", "topic_group": "数据库"},
        {"id": 2, "title": "视频里的编译器设计", "topic_group": "编译器"},
    ]

    result = filter_recommendation_rows(rows, ["视频"])

    assert [row["id"] for row in result] == [1, 2]


def test_single_item_push_does_not_restore_a_fuzzy_dislike_match() -> None:
    rows = [{"id": 1, "title": "运动康复的居家训练指南"}]

    result = filter_recommendation_rows(
        rows,
        ["运动康复"],
        restore_on_total_fuzzy_match=False,
    )

    assert result == []


def test_dislike_digest_is_order_independent_and_changes_with_policy() -> None:
    first = disliked_topics_digest(["运动康复", "营销软文"])
    reordered = disliked_topics_digest([" 营销软文 ", "运动康复", "运动康复"])
    changed = disliked_topics_digest(["运动康复"])

    assert first == reordered
    assert first != changed


# --- Scope discipline: bare category words are labels, not prose filters ----
#
# Origin (2026-11-04): one video literally titled "科普" was disliked once.
# The analyzer stored the bare category word, and the substring rule then
# removed 198 cached rows — including 天文科普 items the same profile still
# listed as interests.


def test_bare_nouns_are_category_terms_but_compounds_are_phrases() -> None:
    assert dislike_term_scope("科普") == "category"
    assert dislike_term_scope("崩坏") == "category"
    assert dislike_term_scope("综艺") == "category"
    # Existing topic exclusions keep working: four characters can distinguish
    # a pattern from its parent category.
    assert dislike_term_scope("运动康复") == "phrase"
    assert dislike_term_scope("五分钟讲清式内容") == "phrase"
    # A qualifier marker makes even a short term a phrase.
    assert dislike_term_scope("标题党") == "phrase"


def test_phrase_selection_normalizes_and_drops_bare_nouns() -> None:
    assert phrase_dislike_terms(["科普", " 运动康复 ", "崩坏"]) == ["运动康复"]


def test_bare_category_does_not_substring_match_a_title() -> None:
    row = {"title": "【硬核科普】从零开始认识显卡", "topic_group": "科技"}

    assert recommendation_row_matches(row, ["科普"]) is False


def test_bare_category_still_excludes_an_exact_structured_topic() -> None:
    row = {"title": "某条被清池的内容", "topic": "科普", "topic_group": "科普"}

    assert recommendation_row_matches_exact(row, ["科普"]) is True
    assert recommendation_row_matches(row, ["科普"]) is True


def test_structured_topic_match_is_equality_not_containment() -> None:
    row = {"title": "天文科普：黑洞有多大", "topic": "科学科普"}

    assert recommendation_row_matches_exact(row, ["科普"]) is False


def test_filter_keeps_liked_topic_rows_when_dislike_is_a_bare_parent() -> None:
    rows = [
        {"id": 1, "title": "天文科普：黑洞有多大"},
        {"id": 2, "title": "健康科普：睡眠周期的真相"},
        {"id": 3, "title": "一条真的被标记不喜欢的内容", "topic": "科普"},
    ]

    result = filter_recommendation_rows(rows, ["科普"])

    assert [row["id"] for row in result] == [1, 2]


def test_filter_still_drops_phrase_matches() -> None:
    rows = [
        {"id": 1, "title": "普通的一条内容"},
        {"id": 2, "title": "五分钟讲清式内容：三分钟看完相对论"},
    ]

    result = filter_recommendation_rows(rows, ["五分钟讲清式内容"])

    assert [row["id"] for row in result] == [1]


def test_engine_serve_filter_uses_the_same_scope_rule() -> None:
    """The engine duplicates this predicate; keep both serve paths in parity."""

    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.recommendation.engine import RecommendationEngine

    prose_hit = DiscoveredContent(title="【硬核科普】从零开始认识显卡")
    assert RecommendationEngine._matches_disliked_topic(prose_hit, ["科普"]) is False

    exact_hit = DiscoveredContent(title="一条内容", topic_key="科普")
    assert RecommendationEngine._matches_disliked_topic(exact_hit, ["科普"]) is True
