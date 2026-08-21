"""Regression tests for mobile web view-model normalization helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

_NODE = shutil.which("node")
ROOT = Path(__file__).resolve().parents[1]


def test_mobile_probe_action_copy_is_canonical() -> None:
    view_models = (ROOT / "src/openbiliclaw/web/js/view-models.js").read_text(encoding="utf-8")

    assert 'label: "确认喜欢", action: "confirm"' in view_models
    assert 'label: "暂时搁置", action: "defer"' in view_models
    assert 'label: "确认不喜欢", action: "reject"' in view_models
    assert 'label: "确认避雷", action: "confirm"' in view_models
    assert 'label: "搁置避雷", action: "defer"' in view_models
    assert 'label: "不是雷点", action: "reject"' in view_models


def test_mobile_profile_uses_probe_action_descriptors() -> None:
    profile_js = (ROOT / "src/openbiliclaw/web/js/views/profile.js").read_text(encoding="utf-8")
    assert "getProbeMessageActions" in profile_js
    assert "getAvoidanceProbeMessageActions" in profile_js
    assert 'data-action="${action.action}"' in profile_js
    assert "const action = e.target.dataset.action" in profile_js
    assert 'data-action="confirm">\\u2713</button>' not in profile_js
    assert 'data-action="reject">\\u2717</button>' not in profile_js
    assert profile_js.count('const buttons = [...row.querySelectorAll(".spec-btn")];') == 2
    assert (
        profile_js.count("for (const actionButton of buttons) actionButton.disabled = true;") == 2
    )
    assert (
        profile_js.count("for (const actionButton of buttons) actionButton.disabled = false;") == 2
    )


def test_mobile_probe_action_buttons_have_explicit_button_type() -> None:
    profile_js = (ROOT / "src/openbiliclaw/web/js/views/profile.js").read_text(encoding="utf-8")
    chat_js = (ROOT / "src/openbiliclaw/web/js/views/chat.js").read_text(encoding="utf-8")

    assert '<button type="button" class="${classes}"' in profile_js
    assert '<button type="button" class="message-action-btn ${item.primary' in chat_js


def _run_js(script: str) -> subprocess.CompletedProcess[str]:
    assert _NODE, "node is required"
    return subprocess.run(
        [_NODE, "--input-type=module", "-e", script],
        cwd=".",
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_js(script: str) -> None:
    result = _run_js(script)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(_NODE is None, reason="node is required for mobile web JS view-model tests")
class TestMobileWebViewModels:
    """Phase 1 view-model coverage."""

    def test_existing_helpers_still_work(self) -> None:
        """Backward compatibility for legacy mobile web helpers."""
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getCoverImageAttrs, normalizeChatTurn, normalizeCoverUrl,
              normalizeMbtiDimensions, normalizePoolStatus,
            } from "./src/openbiliclaw/web/js/view-models.js";

            assert.deepEqual(
              normalizePoolStatus({
                pool_available_count: 561,
                last_replenished_count: 1,
                recent_pool_topics: ["相关推荐", "站内热榜"],
              }),
              { pool_size: 561, recent_replenish: 1, current_topic: "相关推荐" },
            );

            assert.deepEqual(
              normalizeMbtiDimensions({
                type: "INTJ",
                dimensions: {
                  EI: { pole: "I", strength: 0.8 },
                  SN: { pole: "N", strength: 0.6 },
                },
              }),
              [
                { left: "E", right: "I", score: 0.9 },
                { left: "S", right: "N", score: 0.8 },
              ],
            );

            assert.equal(
              normalizeChatTurn({
                turn_id: "m-1",
                message: "ping",
                reply: "pong",
                status: "completed",
              }).response,
              "pong",
            );

            assert.equal(normalizeCoverUrl("http://i2.hdslb.com/bfs/archive/demo.jpg"), "https://i2.hdslb.com/bfs/archive/demo.jpg");
            assert.equal(normalizeCoverUrl("//i1.hdslb.com/bfs/archive/demo.jpg"), "https://i1.hdslb.com/bfs/archive/demo.jpg");
            assert.equal(
              normalizeCoverUrl("https://sns-webpic-qc.xhscdn.com/demo.jpg"),
              "https://sns-webpic-qc.xhscdn.com/demo.jpg",
            );
            assert.deepEqual(
              getCoverImageAttrs("https://i1.hdslb.com/bfs/archive/demo.jpg"),
              { src: "/api/image-proxy?url=https%3A%2F%2Fi1.hdslb.com%2Fbfs%2Farchive%2Fdemo.jpg" },
            );
            assert.equal(getCoverImageAttrs("not-a-url"), null);
        """)
        )

    def test_export_presence(self) -> None:
        """All Phase 1 helpers are exported."""
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import * as vm from "./src/openbiliclaw/web/js/view-models.js";

            const required = [
              "buildVideoUrl", "buildYouTubeUrl", "buildTwitterUrl",
              "buildContentUrl", "buildRecommendationClickPayload",
              "normalizeRecommendation", "normalizeDelightCandidate",
              "getDelightUiState", "getDelightActionState",
              "buildFeedbackPayload", "validateCommentInput", "getCommentSubmitUiState",
              "normalizeProfileSummary", "normalizeCognitionUpdateCard",
              "getMbtiDisplayState", "getProfileStyleDisplay", "getContextPatternRows",
              "getMobileChatSession", "getDelightMessageActions", "getProbeMessageActions",
              "getMobileRecommendationHeaderState",
              "buildNextCognitionHistoryState",
              "normalizeActivityFeed", "getActivityCardState",
              "getPoolStatusSummary", "normalizeRuntimeStatus", "mergeRuntimeStatusEvent",
              "getReadyRecommendationHint",
              "reconcileRecommendationReplacement",
              "getRecommendationCoverPreloadUrls", "getRecommendationImageLoadingAttrs",
              "shouldAutoAppendRecommendations",
              "formatRelativeTimestamp", "formatPublishedTime", "getPublishedTimeDisplay",
              "normalizeSourcePlatform", "getSourceLabel",
              "normalizeCoverUrl", "getCoverImageAttrs",
              "normalizePoolStatus", "normalizeMbtiDimensions", "normalizeChatTurn",
            ];
            for (const name of required) {
                assert.equal(typeof vm[name], "function", `missing export: ${name}`);
            }
        """)
        )

    def test_empty_replacement_preserves_visible_recommendations(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              reconcileRecommendationReplacement,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const current = [{ id: 1, title: "正在看的推荐" }];
            const preserved = reconcileRecommendationReplacement(current, []);
            assert.equal(preserved.preserved, true);
            assert.equal(preserved.items, current);

            const incoming = [{ id: 2, title: "新一批推荐" }];
            assert.deepEqual(
              reconcileRecommendationReplacement(current, incoming),
              { items: incoming, preserved: false },
            );
            assert.deepEqual(
              reconcileRecommendationReplacement([], []),
              { items: [], preserved: false },
            );
        """)
        )

    def test_publication_time_prefers_exact_time_and_falls_back_safely(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              formatPublishedTime,
              normalizeDelightCandidate,
              normalizeRecommendation,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const now = new Date(2026, 6, 11, 12, 0, 0, 0).getTime();
            const iso = (offset) => new Date(now + offset).toISOString();
            const exact = normalizeRecommendation({
              id: 1,
              bvid: "BV1",
              published_at: iso(-10_800_000),
              published_label: "fallback",
            });
            const delight = normalizeDelightCandidate({
              bvid: "BV2",
              published_at: "",
              published_label: "  3   days ago\\n",
            });

            assert.equal(exact.published_at, iso(-10_800_000));
            assert.equal(exact.published_label, "fallback");
            assert.equal(delight.published_label, "3 days ago");

            const cases = [
              [exact, "3 小时前"],
              [{ published_label: "  3   天前\\n" }, "3 天前"],
              [{}, ""],
              [{ published_at: "not-a-date", published_label: "来源时间" }, "来源时间"],
              [{ published_at: iso(-59_999) }, "刚刚"],
              [{ published_at: iso(-60_000) }, "1 小时前"],
              [{ published_at: iso(-86_399_999) }, "23 小时前"],
              [{ published_at: iso(-86_400_000) }, "1 天前"],
              [{ published_at: iso(-604_799_999) }, "6 天前"],
              [{ published_at: iso(-604_800_000) }, "7月4日"],
              [{ published_at: new Date(2026, 0, 2, 12).toISOString() }, "1月2日"],
              [{ published_at: new Date(2025, 10, 9, 12).toISOString() }, "2025-11-09"],
              [{ published_at: iso(300_000) }, "刚刚"],
              [{ published_at: iso(300_001) }, "7月11日"],
            ];
            for (const [item, expected] of cases) {
              assert.equal(formatPublishedTime(item, now), expected);
            }
        """)
        )

    def test_publication_display_exposes_tooltip_only_for_valid_exact_time(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { getPublishedTimeDisplay } from "./src/openbiliclaw/web/js/view-models.js";

            const now = new Date(2026, 6, 11, 12, 0, 0, 0).getTime();
            const exactAt = new Date(now - 10_800_000).toISOString();
            assert.deepEqual(getPublishedTimeDisplay({
              published_at: exactAt,
              published_label: "fallback",
            }, now), {
              text: "3 小时前",
              title: new Date(exactAt).toLocaleString(),
            });
            assert.deepEqual(getPublishedTimeDisplay({ published_label: "3 天前" }, now), {
              text: "3 天前",
              title: "",
            });
            assert.deepEqual(getPublishedTimeDisplay({
              published_at: "not-a-date",
              published_label: "来源时间",
            }, now), {
              text: "来源时间",
              title: "",
            });
            assert.equal(getPublishedTimeDisplay({}, now), null);
        """)
        )

    def test_youtube_recommendation_url_and_click_payload_are_source_aware(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              buildContentUrl,
              buildRecommendationClickPayload,
              normalizeRecommendation,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const item = normalizeRecommendation({
              id: 42,
              bvid: "KPoJ7p9iy4Q",
              content_id: "KPoJ7p9iy4Q",
              title: "A YouTube deep dive",
              source_platform: "youtube",
            });
            const url = buildContentUrl(item);

            assert.equal(url, "https://www.youtube.com/watch?v=KPoJ7p9iy4Q");
            assert.deepEqual(buildRecommendationClickPayload(item, url), {
              bvid: "KPoJ7p9iy4Q",
              content_id: "KPoJ7p9iy4Q",
              content_url: "https://www.youtube.com/watch?v=KPoJ7p9iy4Q",
              source_platform: "youtube",
              title: "A YouTube deep dive",
              recommendation_id: 42,
              topic_label: "",
              up_name: "这位创作者还没认出来",
            });
        """)
        )

    def test_x_recommendation_source_platform_and_label_are_source_aware(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              buildContentUrl,
              buildRecommendationClickPayload,
              getSourceLabel,
              normalizeRecommendation,
              normalizeSourcePlatform,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const tweet = normalizeRecommendation({
              id: 1790000000000000001,
              bvid: "1790000000000000001",
              content_id: "1790000000000000001",
              content_url: "https://x.com/h/status/1790000000000000001",
              source_platform: "x",
              title: "a tweet",
              content_type: "tweet",
              body_text: "tweet body",
              cover_url: "",
            });
            const tweetWithoutUrl = normalizeRecommendation({
              id: 1790000000000000002,
              content_id: "1790000000000000002",
              source_platform: "x",
              title: "another tweet",
              content_type: "tweet",
              body_text: "another tweet body",
              cover_url: "",
            });
            const fallbackUrl = buildContentUrl(tweetWithoutUrl);

            assert.equal(tweet.source_platform, "twitter");
            assert.equal(getSourceLabel(tweet.source_platform), "X (Twitter)");
            assert.equal(fallbackUrl, "https://x.com/i/status/1790000000000000002");
            assert.equal(
              buildRecommendationClickPayload(tweetWithoutUrl, fallbackUrl).source_platform,
              "twitter",
            );
            assert.equal(
              buildRecommendationClickPayload(tweetWithoutUrl, fallbackUrl).content_url,
              "https://x.com/i/status/1790000000000000002",
            );
            assert.equal(
              normalizeSourcePlatform({ content_url: "https://twitter.com/h/status/1" }),
              "twitter",
            );
            assert.equal(
              normalizeSourcePlatform({ content_url: "https://notx.com/h/status/1" }),
              "web",
            );
            assert.equal(
              normalizeSourcePlatform({ source_platform: "twitter" }),
              "twitter",
            );
        """)
        )

    def test_zhihu_recommendation_source_platform_url_and_text_card_are_source_aware(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              buildContentUrl,
              buildRecommendationClickPayload,
              getRecommendationCardKind,
              getSourceLabel,
              normalizeRecommendation,
              normalizeSourcePlatform,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const answer = normalizeRecommendation({
              id: 43,
              content_id: "answer:123",
              content_url: "https://www.zhihu.com/question/1/answer/123",
              title: "一个知乎回答",
              source_platform: "",
              content_type: "answer",
              body_text: "知乎回答正文",
              cover_url: "https://static.zhihu.com/cover.jpg",
            });
            const answerUrl = buildContentUrl(answer);
            const answerCard = getRecommendationCardKind(answer);

            assert.equal(answer.source_platform, "zhihu");
            assert.equal(getSourceLabel(answer.source_platform), "知乎");
            assert.equal(answerUrl, "https://www.zhihu.com/question/1/answer/123");
            assert.equal(answerCard.kind, "text");
            assert.equal(answerCard.coverUrl, "");
            assert.equal(answerCard.text, "知乎回答正文");
            assert.deepEqual(buildRecommendationClickPayload(answer, answerUrl), {
              bvid: "answer:123",
              content_id: "answer:123",
              content_url: "https://www.zhihu.com/question/1/answer/123",
              source_platform: "zhihu",
              title: "一个知乎回答",
              recommendation_id: 43,
              topic_label: "",
              up_name: "这位创作者还没认出来",
            });

            const missingUrl = normalizeRecommendation({
              content_id: "answer:456",
              title: "缺 URL 的知乎回答",
              source_platform: "zh",
              content_type: "answer",
            });
            assert.equal(missingUrl.source_platform, "zhihu");
            assert.equal(buildContentUrl(missingUrl), "");
            assert.equal(
              normalizeSourcePlatform({ content_url: "https://zhuanlan.zhihu.com/p/123" }),
              "zhihu",
            );
          """)
        )

    def test_reddit_recommendation_source_platform_url_and_text_card_are_source_aware(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              buildContentUrl,
              buildRecommendationClickPayload,
              getRecommendationCardKind,
              getSourceLabel,
              normalizeRecommendation,
              normalizeSourcePlatform,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const post = normalizeRecommendation({
              id: 44,
              content_id: "t3_abc123",
              content_url: "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
              title: "Local-first agents",
              source_platform: "rd",
              content_type: "post",
              body_text: "A practical write-up.",
              cover_url: "",
            });
            const postUrl = buildContentUrl(post);
            const postCard = getRecommendationCardKind(post);

            assert.equal(post.source_platform, "reddit");
            assert.equal(getSourceLabel(post.source_platform), "Reddit");
            assert.equal(postUrl, "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/");
            assert.equal(postCard.kind, "text");
            assert.equal(postCard.coverUrl, "");
            assert.equal(postCard.text, "A practical write-up.");
            assert.deepEqual(buildRecommendationClickPayload(post, postUrl), {
              bvid: "t3_abc123",
              content_id: "t3_abc123",
              content_url: "https://www.reddit.com/r/LocalLLaMA/comments/abc123/local_first_agents/",
              source_platform: "reddit",
              title: "Local-first agents",
              recommendation_id: 44,
              topic_label: "",
              up_name: "这位创作者还没认出来",
            });

            const missingUrl = normalizeRecommendation({
              content_id: "t3_missing",
              title: "缺 URL 的 Reddit 帖子",
              source_platform: "reddit",
              content_type: "post",
            });
            assert.equal(missingUrl.source_platform, "reddit");
            assert.equal(buildContentUrl(missingUrl), "");
            assert.equal(
              normalizeSourcePlatform({
                content_url: "https://old.reddit.com/r/test/comments/1/post",
              }),
              "reddit",
            );
          """)
        )

    def test_linuxdo_recommendation_uses_canonical_topic_url_and_text_card(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              buildContentUrl,
              getRecommendationCardKind,
              getSourceLabel,
              normalizeRecommendation,
              normalizeSourcePlatform,
              recommendationStats,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const post = normalizeRecommendation({
              id: 45,
              content_id: "topic:12345",
              title: "Linux.do 上的一篇主题",
              source_platform: "linux.do",
              content_type: "post",
              body_text: "主题正文摘要",
              cover_url: "",
            });

            assert.equal(post.source_platform, "linuxdo");
            assert.equal(post.up_name, "这位创作者还没认出来");
            assert.equal(getSourceLabel(post.source_platform), "Linux.do");
            assert.equal(buildContentUrl(post), "https://linux.do/t/12345");
            assert.deepEqual(getRecommendationCardKind(post), {
              kind: "text",
              coverUrl: "",
              text: "主题正文摘要",
            });
            assert.equal(
              normalizeSourcePlatform({
                content_url: "https://linux.do/t/example/67890",
              }),
              "linuxdo",
            );
            assert.equal(
              normalizeSourcePlatform({
                content_url: "https://notlinux.do/t/example/67890",
              }),
              "web",
            );
          """)
        )

    def test_weibo_recommendation_alias_hosts_and_text_card_are_source_aware(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              buildContentUrl,
              getRecommendationCardKind,
              getSourceLabel,
              normalizeRecommendation,
              normalizeSourcePlatform,
              recommendationStats,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const post = normalizeRecommendation({
              content_id: "5023456789012345",
              content_url: "https://m.weibo.cn/detail/5023456789012345",
              title: "一条公开微博",
              source_platform: "wb",
              content_type: "post",
              body_text: "公开微博正文。",
              cover_url: "https://wx1.sinaimg.cn/large/example.jpg",
              share_count: 321,
            });

            assert.equal(post.source_platform, "weibo");
            assert.equal(getSourceLabel(post.source_platform), "微博");
            assert.equal(buildContentUrl(post), "https://m.weibo.cn/detail/5023456789012345");
            assert.equal(getRecommendationCardKind(post).kind, "text");
            assert.match(recommendationStats(post), /🔁 321/);
            assert.equal(
              normalizeSourcePlatform({ content_url: "https://weibo.com/u/123" }),
              "weibo",
            );
            assert.equal(
              normalizeSourcePlatform({ content_url: "https://wx1.sinaimg.cn/large/a.jpg" }),
              "weibo",
            );

            const missingUrl = normalizeRecommendation({
              content_id: "5023456789012346",
              source_platform: "weibo",
              content_type: "post",
            });
            assert.equal(buildContentUrl(missingUrl), "");
          """)
        )

    def test_mobile_cover_templates_use_wrapper_fallbacks(self) -> None:
        recommend_js = Path("src/openbiliclaw/web/js/views/recommend.js").read_text()
        chat_js = Path("src/openbiliclaw/web/js/views/chat.js").read_text()
        app_css = Path("src/openbiliclaw/web/css/app.css").read_text()

        assert 'referrerpolicy="${cover.referrerPolicy}"' not in recommend_js
        assert 'referrerpolicy="${cover.referrerPolicy}"' not in chat_js
        assert '? `<img class="card-cover"' not in recommend_js
        assert 'onerror="this.remove()"' not in recommend_js
        assert "card-cover-frame" in recommend_js
        assert "card-cover-frame" in app_css
        assert ".card-cover-frame.is-error" in app_css
        assert ".card-cover::after" not in app_css

    def test_runtime_status_normalizes_pool_readiness_counts(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              mergeRuntimeStatusEvent,
              normalizeRuntimeStatus,
            } from "./src/openbiliclaw/web/js/view-models.js";

            assert.deepEqual(
              normalizeRuntimeStatus({ initialized: true }),
              {
                initialized: true,
                recommendation_count: 0,
                pending_signal_events: 0,
                last_refresh_at: "",
                last_notification_at: "",
                unread_count: 0,
                pool_available_count: 0,
                pool_raw_count: 0,
                pool_pending_count: 0,
                pool_target_count: 0,
                last_discovered_count: 0,
                last_replenished_count: 0,
                recent_pool_topics: [],
                manual_refresh_state: "idle",
                manual_refresh_message: "",
              },
            );

            const merged = mergeRuntimeStatusEvent(
              { initialized: true, pool_available_count: 0 },
              {
                pool_available_count: 10,
                pool_raw_count: 152,
                pool_pending_count: 142,
              },
            );
            assert.equal(merged.pool_available_count, 10);
            assert.equal(merged.pool_raw_count, 152);
            assert.equal(merged.pool_pending_count, 142);
        """)
        )

    def test_recommendation_cover_preload_helpers(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getRecommendationCoverPreloadUrls,
              getRecommendationImageLoadingAttrs,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const tenItems = Array.from({ length: 10 }, (_, index) => ({
              cover_url: `https://i${index}.hdslb.com/bfs/archive/${index}.jpg`,
            }));
            assert.equal(getRecommendationCoverPreloadUrls(tenItems).length, 10);

            const urls = getRecommendationCoverPreloadUrls([
              { cover_url: "http://i0.hdslb.com/bfs/archive/a.jpg" },
              { cover_url: "//i0.hdslb.com/bfs/archive/a.jpg" },
              { cover_url: "https://i1.hdslb.com/bfs/archive/b.jpg" },
              { cover_url: "not-a-url" },
              { cover_url: "" },
              { cover_url: "https://sns-webpic-qc.xhscdn.com/c.jpg" },
            ], { limit: 3 });

            assert.deepEqual(urls, [
              "/api/image-proxy?url=https%3A%2F%2Fi0.hdslb.com%2Fbfs%2Farchive%2Fa.jpg",
              "/api/image-proxy?url=https%3A%2F%2Fi1.hdslb.com%2Fbfs%2Farchive%2Fb.jpg",
              "/api/image-proxy?url=https%3A%2F%2Fsns-webpic-qc.xhscdn.com%2Fc.jpg",
            ]);

            assert.deepEqual(
              getRecommendationImageLoadingAttrs(0),
              { loading: "eager", fetchPriority: "high" },
            );
            assert.deepEqual(
              getRecommendationImageLoadingAttrs(1),
              { loading: "eager", fetchPriority: "high" },
            );
            assert.deepEqual(
              getRecommendationImageLoadingAttrs(2),
              { loading: "eager", fetchPriority: "auto" },
            );
            assert.deepEqual(
              getRecommendationImageLoadingAttrs(11),
              { loading: "eager", fetchPriority: "auto" },
            );
            assert.deepEqual(
              getRecommendationImageLoadingAttrs(12),
              { loading: "eager", fetchPriority: "auto" },
            );
            assert.deepEqual(
              getRecommendationImageLoadingAttrs(999),
              { loading: "eager", fetchPriority: "auto" },
            );
            assert.deepEqual(
              getRecommendationImageLoadingAttrs(12, { eagerCount: 12 }),
              { loading: "lazy", fetchPriority: "auto" },
            );
        """)
        )

    def test_mobile_recommendation_view_preloads_and_auto_appends(self) -> None:
        recommend_js = Path("src/openbiliclaw/web/js/views/recommend.js").read_text()

        assert "getRecommendationCoverPreloadUrls" in recommend_js
        assert "getRecommendationImageLoadingAttrs" in recommend_js
        assert "function warmRecommendationCovers" in recommend_js
        assert "new Image()" in recommend_js
        assert ".decode()" in recommend_js
        assert "waitForDecode" in recommend_js
        assert "CARD_EAGER_COVER_COUNT" in recommend_js
        assert "eagerCount: CARD_EAGER_COVER_COUNT" in recommend_js
        assert (
            "void warmRecommendationCovers(newItems, { limit: CARD_EAGER_COVER_COUNT });"
            in recommend_js
        )
        assert 'loading="${esc(imageAttrs.loading)}"' in recommend_js
        assert 'fetchpriority="${esc(imageAttrs.fetchPriority)}"' in recommend_js
        assert "AUTO_APPEND_ROOT_MARGIN" in recommend_js
        assert "IntersectionObserver" in recommend_js
        assert "observeAutoAppendSentinel" in recommend_js
        assert ".load-more-row" in recommend_js
        assert "handleAppend();" in recommend_js
        assert "shouldAutoAppendRecommendations" in recommend_js
        assert "autoAppendUserArmed" in recommend_js

    def test_mobile_auto_append_requires_user_scroll_intent(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              shouldAutoAppendRecommendations,
            } from "./src/openbiliclaw/web/js/view-models.js";

            assert.equal(
              shouldAutoAppendRecommendations({
                loading: false,
                autoAppendExhausted: false,
                activeTab: "recommend",
                userArmed: false,
              }),
              false,
            );
            assert.equal(
              shouldAutoAppendRecommendations({
                loading: false,
                autoAppendExhausted: false,
                activeTab: "recommend",
                userArmed: true,
              }),
              true,
            );
            assert.equal(
              shouldAutoAppendRecommendations({
                loading: true,
                autoAppendExhausted: false,
                activeTab: "recommend",
                userArmed: true,
              }),
              false,
            );
            assert.equal(
              shouldAutoAppendRecommendations({
                loading: false,
                autoAppendExhausted: true,
                activeTab: "recommend",
                userArmed: true,
              }),
              false,
            );
            assert.equal(
              shouldAutoAppendRecommendations({
                loading: false,
                autoAppendExhausted: false,
                activeTab: "profile",
                userArmed: true,
              }),
              false,
            );
        """)
        )

    def test_normalize_recommendation_defaults(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { normalizeRecommendation } from "./src/openbiliclaw/web/js/view-models.js";

            const rec = normalizeRecommendation({ id: 42, bvid: "BV1xx" });
            assert.equal(rec.id, 42);
            assert.equal(rec.bvid, "BV1xx");
            assert.equal(rec.title, "这条标题还没对上号");
            assert.equal(rec.up_name, "这位 UP 还没认出来");
            assert.equal(rec.source_platform, "bilibili");
        """)
        )

    def test_build_feedback_payload(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { buildFeedbackPayload } from "./src/openbiliclaw/web/js/view-models.js";

            const p = buildFeedbackPayload(42, "like", "  nice  ");
            assert.equal(p.recommendation_id, 42);
            assert.equal(p.feedback_type, "like");
            assert.equal(p.note, "nice");

            const p2 = buildFeedbackPayload("99", "comment");
            assert.equal(p2.recommendation_id, 99);
            assert.equal(p2.note, "");
        """)
        )

    def test_delight_action_state(self) -> None:
        """getDelightActionState maps UI actions to backend-safe API tokens."""
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { getDelightActionState } from "./src/openbiliclaw/web/js/view-models.js";

            const view = getDelightActionState("view");
            assert.equal(view.apiResponse, "view");
            assert.equal(view.uiState, "viewed");
            assert.equal(view.permanent, false);

            const reject = getDelightActionState("reject");
            assert.equal(reject.apiResponse, "dislike");
            assert.equal(reject.uiState, "rejected");
            assert.equal(reject.permanent, true);

            const like = getDelightActionState("like");
            assert.equal(like.apiResponse, "like");
            assert.equal(like.uiState, "liked");
            assert.equal(like.permanent, false);

            const chat = getDelightActionState("chat");
            assert.equal(chat.apiResponse, null);
            assert.equal(chat.uiState, "chatting");
            assert.equal(chat.permanent, false);

            const unknown = getDelightActionState("unknown");
            assert.equal(unknown.apiResponse, null);
            assert.equal(unknown.uiState, "pending");
            assert.equal(unknown.permanent, false);
        """)
        )

    def test_delight_ui_state(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { getDelightUiState } from "./src/openbiliclaw/web/js/view-models.js";

            const pending = getDelightUiState({ bvid: "BV1", title: "t", delight_score: 0.9 });
            assert.deepEqual(pending, {
              visible: true,
              highlighted: false,
              handled: false,
              show_status: false,
              show_actions: true,
              like_pressed: false,
              like_disabled: false,
              score_label: "大概率会戳中你",
              response_tone: "info",
              response_message: "",
            });

            const viewed = getDelightUiState({ bvid: "BV1", state: "viewed", delight_score: 0.7 });
            assert.deepEqual(viewed, {
              visible: true,
              highlighted: false,
              handled: true,
              show_status: true,
              show_actions: false,
              like_pressed: false,
              like_disabled: true,
              score_label: "这条可能会拐到你",
              response_tone: "success",
              response_message: "已打开，阿B 会把这次点击当成强信号。",
            });

            const liked = getDelightUiState({ bvid: "BV1", state: "liked", delight_score: 0.7 });
            assert.deepEqual(liked, {
              visible: true,
              highlighted: false,
              handled: false,
              show_status: true,
              show_actions: true,
              like_pressed: true,
              like_disabled: true,
              score_label: "这条可能会拐到你",
              response_tone: "success",
              response_message: "好，这类多来点。",
            });

            const rejected = getDelightUiState({
              bvid: "BV1",
              state: "rejected",
              delight_score: 0.7,
            });
            assert.deepEqual(rejected, {
              visible: true,
              highlighted: false,
              handled: true,
              show_status: true,
              show_actions: false,
              like_pressed: false,
              like_disabled: true,
              score_label: "这条可能会拐到你",
              response_tone: "info",
              response_message: "记下了，这类惊喜先少来点。",
            });

            const chatted = getDelightUiState({
                bvid: "BV1",
                state: "chatted",
                delight_score: 0.7,
            });
            assert.deepEqual(chatted, {
              visible: true,
              highlighted: false,
              handled: false,
              show_status: true,
              show_actions: true,
              like_pressed: false,
              like_disabled: false,
              score_label: "这条可能会拐到你",
              response_tone: "info",
              response_message: "这句已经记下，后面会更会试探。",
            });

            const empty = getDelightUiState({});
            assert.deepEqual(empty, {
              visible: false,
              highlighted: false,
              handled: false,
              show_status: false,
              show_actions: false,
              like_pressed: false,
              like_disabled: false,
              score_label: "",
              response_tone: "info",
              response_message: "",
            });
        """)
        )

    def test_chat_alignment_helpers(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getAvoidanceProbeMessageActions,
              getDelightMessageActions,
              getMobileChatSession,
              getProbeMessageActions,
            } from "./src/openbiliclaw/web/js/view-models.js";

            assert.deepEqual(getMobileChatSession(), { session: "popup", scope: "chat" });
            assert.deepEqual(
              getMobileChatSession("delight"),
              { session: "popup", scope: "delight" },
            );

            assert.deepEqual(
              getDelightMessageActions().map((item) => [item.label, item.action]),
              [
                ["看看", "view"],
                ["喜欢", "like"],
                ["不感兴趣", "reject"],
                ["聊一聊", "chat"],
              ],
            );
            assert.deepEqual(
              getProbeMessageActions().map((item) => [item.label, item.action]),
              [
                ["确认喜欢", "confirm"],
                ["暂时搁置", "defer"],
                ["确认不喜欢", "reject"],
                ["多聊聊", "chat"],
              ],
            );
            assert.deepEqual(
              getAvoidanceProbeMessageActions().map((item) => [item.label, item.action]),
              [
                ["确认避雷", "confirm"],
                ["搁置避雷", "defer"],
                ["不是雷点", "reject"],
                ["多聊聊", "chat"],
              ],
            );
        """)
        )

    def test_pool_status_summary_semantic(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { getPoolStatusSummary } from "./src/openbiliclaw/web/js/view-models.js";

            // Uninit returns null
            assert.equal(getPoolStatusSummary({}), null);

            // Running with items
            const running = getPoolStatusSummary({
              initialized: true,
              pool_available_count: 20,
              pool_target_count: 30,
              manual_refresh_state: "running",
            });
            assert.equal(running.available, "还有 20 条可换");
            assert.equal(running.replenished, "后台继续在找更多");

            // Idle with recent replenish
            const idle = getPoolStatusSummary({
              initialized: true,
              pool_available_count: 34,
              pool_target_count: 30,
              last_replenished_count: 6,
              recent_pool_topics: ["游戏", "编程"],
              manual_refresh_state: "idle",
            });
            assert.equal(idle.available, "还有 34 条可换");
            assert.equal(idle.replenished, "刚补进 6 条");
            assert.equal(idle.topics, "游戏 / 编程");

            const internal = getPoolStatusSummary({
              initialized: true,
              pool_available_count: 600,
              pool_target_count: 600,
              last_replenished_count: 1,
              recent_pool_topics: ["xhs-extension-task", "xhs-extension-explore"],
              manual_refresh_state: "idle",
            });
            assert.equal(internal.topics, "小红书任务 / 小红书探索");

            const pending = getPoolStatusSummary({
              initialized: true,
              pool_available_count: 0,
              pool_pending_count: 142,
              pool_target_count: 300,
              manual_refresh_state: "running",
            });
            assert.equal(pending.available, "找到 142 条素材，正在整理成可换内容");
            assert.equal(pending.replenished, "正在整理");
            assert.equal(pending.topics, "整理好就能换，不会把素材数当可换数");

            const stockedWithBacklog = getPoolStatusSummary({
              initialized: true,
              pool_available_count: 222,
              pool_pending_count: 177,
              pool_target_count: 300,
              manual_refresh_state: "idle",
            });
            assert.equal(stockedWithBacklog.available, "还有 222 条可换");
            assert.equal(stockedWithBacklog.replenished, "另有 177 条素材");
            assert.equal(stockedWithBacklog.topics, "素材已抓到，会按可换库存缺口整理");
        """)
        )

    def test_mobile_recommendation_header_matches_plugin_semantics(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getMobileRecommendationHeaderState,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const header = getMobileRecommendationHeaderState({
              runtimeStatus: {
                initialized: true,
                pool_available_count: 23,
                pool_target_count: 60,
                last_replenished_count: 7,
                recent_pool_topics: ["城市影像", "设备测评"],
              },
              activityFeed: {
                live_summary: "刚补进 7 条，正在筛城市影像",
                headline: "最近活跃：城市影像",
                items: [{
                  id: "a1",
                  kind: "refresh",
                  summary: "候选池完成一次补货",
                  created_at: "刚刚",
                }],
              },
            });

            assert.equal(header.kicker, "For You");
            assert.equal(header.title, "这几条，你大概会点开");
            assert.equal(header.primaryActionLabel, "换一批");
            assert.equal(header.secondaryActionLabel, "加载更多");
            assert.equal(header.activityLine, "刚补进 7 条，正在筛城市影像");
            assert.deepEqual(
              header.poolChips.map((chip) => [chip.label, chip.value, chip.tone]),
              [
                ["当前可换", "23 条", "neutral"],
                ["补货进展", "补进 7 条", "brand"],
                ["现在在忙", "城市影像 / 设备测评", "info"],
              ],
            );

            const internal = getMobileRecommendationHeaderState({
              runtimeStatus: {
                initialized: true,
                pool_available_count: 600,
                pool_target_count: 600,
                last_replenished_count: 1,
                recent_pool_topics: ["xhs-extension-task", "xhs-extension-explore"],
              },
            });
            assert.deepEqual(
              internal.poolChips.map((chip) => [chip.label, chip.value]),
              [
                ["当前可换", "600 条"],
                ["补货进展", "补进 1 条"],
                ["现在在忙", "小红书任务 / 探索"],
              ],
            );

            const pending = getMobileRecommendationHeaderState({
              runtimeStatus: {
                initialized: true,
                pool_available_count: 0,
                pool_pending_count: 142,
                pool_target_count: 300,
                manual_refresh_state: "running",
              },
            });
            assert.deepEqual(
              pending.poolChips.map((chip) => [chip.label, chip.value]),
              [
                ["当前可换", "0 条"],
                ["素材整理", "142 条"],
                ["现在在忙", "整理好就能换，不会把素材数当可换数"],
              ],
            );
        """)
        )

    def test_pool_stream_snapshot_can_initialize_mobile_inventory_summary(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getPoolStatusSummary,
              mergeRuntimeStatusEvent,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const runtime = mergeRuntimeStatusEvent(null, {
              pool_available_count: 23,
              pool_pending_count: 4,
              recent_pool_topics: ["城市影像"],
            });

            assert.equal(runtime.initialized, true);
            assert.equal(getPoolStatusSummary(runtime).available, "还有 23 条可换");
        """),
        )

    def test_normalize_activity_feed(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getActivityCardState,
              normalizeActivityFeed,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const empty = normalizeActivityFeed({});
            assert.equal(empty.items.length, 0);
            assert.equal(empty.live_summary, "");

            const feed = normalizeActivityFeed({
              live_summary: "正在补货",
              items: [{ id: "1", summary: "找到了3条", created_at: "2025-01-01" }],
              has_more: true,
              next_cursor: "abc",
            });
            assert.equal(feed.items.length, 1);
            assert.equal(feed.live_summary, "正在补货");
            assert.equal(feed.has_more, true);

            const card = getActivityCardState({ feed, expanded: false });
            assert.equal(card.line1, "正在补货");
            assert.equal(card.expanded, false);
        """)
        )

    def test_normalize_profile_summary(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { normalizeProfileSummary } from "./src/openbiliclaw/web/js/view-models.js";

            // Empty input gives defaults
            const empty = normalizeProfileSummary({});
            assert.equal(empty.initialized, false);
            assert.equal(empty.personality_portrait, "画像还在慢慢攒，先多看一阵。");
            assert.deepEqual(empty.core_traits, []);
            assert.deepEqual(empty.values, []);
            assert.equal(empty.exploration_openness, 0.5);

            // Full input
            const full = normalizeProfileSummary({
              initialized: true,
              personality_portrait: "test portrait",
              core_traits: ["curious", "  "],
              values: ["truth"],
              likes: [{ domain: "tech", weight: 0.8, specifics: [{ name: "AI" }] }],
              exploration_openness: 0.7,
              favorite_up_users: ["UP1"],
              speculative_interests: [{
                domain: "cooking",
                confidence: 0.6,
                status: "active",
                probe_mode: "bridge",
                challenge: true,
              }],
              speculative_avoidances: [{
                domain: "浅层热点复读",
                reason: "信息密度低",
                source_mode: "negative_signal",
                confidence: 0.7,
                status: "active",
                specifics: [{ name: "标题党热点解读" }],
              }],
            });
            assert.equal(full.initialized, true);
            assert.equal(full.personality_portrait, "test portrait");
            assert.deepEqual(full.core_traits, ["curious"]);
            assert.equal(full.likes.length, 1);
            assert.equal(full.likes[0].specifics[0].name, "AI");
            assert.equal(full.exploration_openness, 0.7);
            assert.deepEqual(full.favorite_up_users, ["UP1"]);
            assert.equal(full.speculative_interests[0].domain, "cooking");
            assert.equal(full.speculative_interests[0].probe_mode, "bridge");
            assert.equal(full.speculative_interests[0].challenge, true);
            assert.equal(full.speculative_avoidances[0].domain, "浅层热点复读");
            assert.equal(full.speculative_avoidances[0].source_mode, "negative_signal");
            assert.equal(full.speculative_avoidances[0].specifics[0].name, "标题党热点解读");
        """)
        )

    def test_profile_confirm_probe_actions_mark_profile_surface(self) -> None:
        api_js = Path("src/openbiliclaw/web/js/api.js").read_text()
        profile_js = Path("src/openbiliclaw/web/js/views/profile.js").read_text()
        desktop_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()

        assert "export async function respondToProbe(domain, responseType, options = {})" in api_js
        assert 'surface: "profile"' in profile_js
        assert "if (!isAvoidance && surface) payload.surface = surface;" in desktop_js
        assert (
            'submitProbeResponse(type, domain, response, { surface: "profile", keepalive })'
            in desktop_js
        )
        assert 'respondToProbe(domain, action, { surface: "profile" })' in profile_js

    def test_profile_edit_interest_specifics_are_editable_in_web_surfaces(self) -> None:
        profile_js = Path("src/openbiliclaw/web/js/views/profile.js").read_text()
        desktop_js = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()

        for source in (profile_js, desktop_js):
            assert "specifics" in source
            assert "edit-specific-list" in source
            assert "data-edit-parent" in source
            assert "添加二级兴趣" in source
            assert "parent:" in source

    def test_normalize_profile_summary_preserves_probe_mode_metadata(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { normalizeProfileSummary } from "./src/openbiliclaw/web/js/view-models.js";

            const profile = normalizeProfileSummary({
              initialized: true,
              speculative_interests: [{ domain: "city", probe_mode: "bridge" }],
            });

            assert.equal(profile.speculative_interests[0].probe_mode, "bridge");
            assert.equal(profile.speculative_interests[0].challenge, true);
        """)
        )

    def test_mobile_avoidance_probe_ui_wiring_is_present(self) -> None:
        api_js = Path("src/openbiliclaw/web/js/api.js").read_text()
        chat_js = Path("src/openbiliclaw/web/js/views/chat.js").read_text()
        profile_js = Path("src/openbiliclaw/web/js/views/profile.js").read_text()
        view_models_js = Path("src/openbiliclaw/web/js/view-models.js").read_text()

        assert "fetchPendingAvoidanceProbes" in api_js
        assert 'requestJson("/avoidance-probes/pending")' in api_js
        assert "respondToAvoidanceProbe" in api_js
        assert 'requestJson("/avoidance-probes/respond"' in api_js

        assert 'type === "avoidance.probe"' in chat_js
        assert '"avoidance_probe"' in chat_js
        assert "getAvoidanceProbeMessageActions" in chat_js
        assert "确认避雷" in view_models_js

        assert "speculative_avoidances" in profile_js
        assert "renderSpecAvoidances" in profile_js
        assert "respondToAvoidanceProbe" in profile_js

    def test_mobile_avoidance_actions_do_not_bind_interest_handler(self) -> None:
        profile_js = Path("src/openbiliclaw/web/js/views/profile.js").read_text()
        interest_binder = (
            "function bindSpecInterestActions()"
            + profile_js.split("function bindSpecInterestActions()", 1)[1].split(
                "\n}\n\n// ── Speculative Avoidances", 1
            )[0]
            + "\n}"
        )
        avoidance_binder = (
            "function bindSpecAvoidanceActions()"
            + profile_js.split("function bindSpecAvoidanceActions()", 1)[1].split(
                "\n}\n\n// ── Cognition Cards", 1
            )[0]
            + "\n}"
        )

        script = (
            dedent("""
            import assert from "node:assert/strict";

            const interestBinderSource = __INTEREST_BINDER__;
            const avoidanceBinderSource = __AVOIDANCE_BINDER__;
            const createBindings = new Function(
              "$root", "state", "patchState", "respondToProbe", "respondToAvoidanceProbe",
              "rememberHandledProbe", "forgetHandledProbe", "render",
              `${interestBinderSource}\n${avoidanceBinderSource}\n` +
                "return { bindSpecInterestActions, bindSpecAvoidanceActions };",
            );

            for (const action of ["confirm", "defer", "reject"]) {
              const row = {
                dataset: { domain: "浅层热点复读" },
                querySelectorAll: () => buttons,
              };
              const buttons = ["confirm", "defer", "reject"].map((buttonAction) => ({
                dataset: { action: buttonAction },
                disabled: false,
                listeners: [],
                addEventListener(_event, listener) { this.listeners.push(listener); },
                closest: () => row,
              }));
              const root = {
                querySelectorAll(selector) {
                  if (selector === ".spec-avoidance .spec-avoidance-btn") return buttons;
                  if (selector === ".spec-interest .spec-btn") return buttons;
                  if (selector === ".spec-interest:not(.spec-avoidance) .spec-btn") return [];
                  throw new Error(`unexpected selector: ${selector}`);
                },
              };
              const calls = { interest: [], avoidance: [] };
              const state = {
                profile: { speculative_interests: [], speculative_avoidances: [] },
              };
              const bindings = createBindings(
                root,
                state,
                () => {},
                async (domain, response) => calls.interest.push([domain, response]),
                async (domain, response) => calls.avoidance.push([domain, response]),
                () => {},
                () => {},
                () => {},
              );

              bindings.bindSpecInterestActions();
              bindings.bindSpecAvoidanceActions();
              const button = buttons.find((candidate) => candidate.dataset.action === action);
              await Promise.all(button.listeners.map((listener) => listener({ target: button })));

              assert.deepEqual(calls.avoidance, [["浅层热点复读", action]]);
              assert.deepEqual(calls.interest, []);
            }
        """)
            .replace("__INTEREST_BINDER__", repr(interest_binder))
            .replace("__AVOIDANCE_BINDER__", repr(avoidance_binder))
        )
        _assert_js(script)

    def test_mobile_probe_card_locks_all_actions_after_tap(self) -> None:
        chat_js = Path("src/openbiliclaw/web/js/views/chat.js").read_text()

        assert "setProbeCardBusy(card, true)" in chat_js
        assert "setProbeCardBusy(card, false)" in chat_js
        assert 'card.querySelectorAll("[data-probe]")' in chat_js

    def test_mobile_empty_messages_overlay_preserves_close_handler(self) -> None:
        chat_js = Path("src/openbiliclaw/web/js/views/chat.js").read_text()

        assert "panel.innerHTML +=" not in chat_js
        assert 'emptyState.className = "messages-empty-state"' in chat_js
        assert "panel.appendChild(emptyState)" in chat_js

    def test_desktop_web_knows_avoidance_probe_endpoint(self) -> None:
        source = Path("src/openbiliclaw/web/desktop/assets/js/app.js").read_text()

        assert "avoidanceProbeRespond" in source
        assert "avoidance.probe" in source
        assert "确认避雷" in source

    def test_profile_display_helpers_preserve_plugin_semantics(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getContextPatternRows,
              getMbtiDisplayState,
              getProfileStyleDisplay,
            } from "./src/openbiliclaw/web/js/view-models.js";

            const mbti = getMbtiDisplayState({
              type: "INTJ",
              confidence: 0.82,
              dimensions: { EI: { pole: "I", strength: 0.74 } },
            });
            assert.equal(mbti.type, "INTJ");
            assert.equal(mbti.confidence_label, "可信度 82%");
            assert.equal(mbti.dimensions[0].left, "E");

            const style = getProfileStyleDisplay({
              preferred_duration: "long",
              preferred_pace: "slow",
              quality_sensitivity: 0.92,
            });
            assert.equal(style.preferred_duration, "长视频");
            assert.equal(style.preferred_pace, "慢节奏");
            assert.equal(style.quality_sensitivity, 0.92);

            const rows = getContextPatternRows({
              weekday_patterns: "工作日晚上更常看深度内容",
              session_type: "研究型长会话",
            });
            assert.deepEqual(
              rows.map((row) => [row.key, row.label, row.value]),
              [
                ["weekday", "工作日", "工作日晚上更常看深度内容"],
                ["session", "模式", "研究型长会话"],
              ],
            );
        """)
        )

    def test_cognition_card_normalization_is_idempotent(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { normalizeCognitionUpdateCard } from "./src/openbiliclaw/web/js/view-models.js";

            const first = normalizeCognitionUpdateCard({
              summary: "更明确偏好因果链",
              context_line: "基于最近几条国际局势视频",
              source: "feedback",
              source_label: "推荐反馈",
              expand_hint: "expandable",
              impact: "推荐表达会更强调结构。",
              reasoning: "连续停留在解释链条完整的视频上。",
              evidence: "观看了两条复盘内容。",
            });
            assert.equal(first.contextLine, "基于最近几条国际局势视频");
            assert.equal(first.source, "feedback");
            assert.equal(first.sourceLabel, "推荐反馈");

            const second = normalizeCognitionUpdateCard(first);
            assert.equal(second.contextLine, "基于最近几条国际局势视频");
            assert.equal(second.source, "feedback");
            assert.equal(second.sourceLabel, "推荐反馈");
            assert.equal(second.expandable, true);
        """)
        )

    def test_format_relative_timestamp(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import { formatRelativeTimestamp } from "./src/openbiliclaw/web/js/view-models.js";

            const now = Date.parse("2025-06-01T12:00:00Z");
            assert.equal(formatRelativeTimestamp("2025-06-01T11:59:30Z", now), "刚刚");
            assert.equal(formatRelativeTimestamp("2025-06-01T11:48:00Z", now), "12 分钟前");
            assert.equal(formatRelativeTimestamp("2025-06-01T09:00:00Z", now), "3 小时前");
            assert.equal(formatRelativeTimestamp("2025-05-30T12:00:00Z", now), "2 天前");
            assert.equal(formatRelativeTimestamp(""), "");
            assert.equal(formatRelativeTimestamp("not-a-date"), "");
        """)
        )

    def test_source_platform_and_label(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
              getSourceLabel,
              normalizeSourcePlatform,
            } from "./src/openbiliclaw/web/js/view-models.js";

            assert.equal(normalizeSourcePlatform({ bvid: "BV1xx" }), "bilibili");
            assert.equal(
              normalizeSourcePlatform({
                content_url: "https://www.youtube.com/watch?v=abc",
              }),
              "youtube",
            );
            assert.equal(normalizeSourcePlatform({ source_platform: "douyin" }), "douyin");
            assert.equal(getSourceLabel("bilibili"), "Bilibili");
            assert.equal(getSourceLabel("youtube"), "YouTube");
            assert.equal(getSourceLabel("unknown"), "unknown");
        """)
        )

    def test_saved_sync_view_model_sanitizes_status_and_extension_copy(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            globalThis.location = { protocol: "http:", host: "127.0.0.1:8420" };
            const {
              getSavedSyncViewModel,
              normalizeSavedListItem,
            } = await import("./src/openbiliclaw/web/js/views/saved.js");

            const normalized = normalizeSavedListItem({
              item_key: "youtube:abc\\u0000",
              source_platform: "youtube",
              content_id: "abc",
              sync_status: "unexpected-status",
              error_message: "unsafe\\u2028message",
            });
            assert.equal(normalized.item_key, "youtube:abc");
            assert.equal(normalized.sync_status, "failed");
            assert.equal(normalized.error_message, "unsafemessage");

            assert.deepEqual(
              getSavedSyncViewModel({
                item_key: "youtube:abc",
                source_platform: "youtube",
                content_id: "abc",
                sync_status: "extension_required",
              }),
              {
                item_key: "youtube:abc",
                source_platform: "youtube",
                content_id: "abc",
                content_url: "",
                content_type: "video",
                title: "abc",
                author_name: "",
                cover_url: "",
                sync_status: "extension_required",
                resolved_target: "",
                error_code: "",
                error_message: "",
                label: "需要连接插件",
                tone: "warning",
                retryable: true,
                detail: "请连接已安装 OpenBiliClaw 插件的登录态浏览器后重试。",
                actionable: true,
                busy: false,
                localOnly: false,
                actionLabel: "重试同步",
              },
            );

            const unsupported = getSavedSyncViewModel({
              item_key: "youtube:abc",
              source_platform: "youtube",
              content_id: "abc",
              sync_status: "unsupported",
              error_code: "unsupported_content_type",
              error_message: "adapter unavailable",
            });
            assert.equal(unsupported.label, "仅本地保存");
            assert.equal(unsupported.tone, "neutral");
            assert.equal(unsupported.retryable, false);
            assert.equal(unsupported.detail, "此内容类型暂不支持平台同步，仅保存在本地。");
            assert.equal(unsupported.actionable, false);
            assert.equal(unsupported.localOnly, true);

            const rollingUpgrade = getSavedSyncViewModel({
              item_key: "youtube:abc",
              source_platform: "youtube",
              content_id: "abc",
              sync_status: "unsupported",
              error_code: "unsupported_adapter_missing",
            });
            assert.equal(rollingUpgrade.retryable, true);
            assert.match(rollingUpgrade.detail, /更新|升级/);

            const supportedPlatforms = [
              "youtube",
              "twitter",
              "xiaohongshu",
              "douyin",
              "zhihu",
              "reddit",
            ];
            for (const platform of supportedPlatforms) {
              const ready = getSavedSyncViewModel({
                item_key: `${platform}:1`,
                source_platform: platform,
                content_id: "1",
              });
              assert.equal(ready.label, "待同步", platform);
              assert.equal(ready.actionable, true, platform);
            }

            for (const sync_status of ["pending", "syncing"]) {
              const busy = getSavedSyncViewModel({
                item_key: "youtube:abc",
                source_platform: "youtube",
                content_id: "abc",
                sync_status,
                sync_task_id: sync_status === "pending" ? "task-1" : "",
              });
              assert.equal(busy.actionable, false);
              assert.equal(busy.busy, true);
            }

            const localPending = getSavedSyncViewModel({
              item_key: "youtube:abc",
              source_platform: "youtube",
              content_id: "abc",
              sync_status: "pending",
              sync_task_id: "",
            });
            assert.equal(localPending.actionable, true);
            assert.equal(localPending.busy, false);
            assert.match(localPending.detail, /手动同步/);
        """)
        )


class TestMobileWebDialogueConfirmationIsReadOnly:
    """Four-surface contract: mobile web shows insights but never settles them.

    Confirming a hypothesis is a durable settlement that must flow through the
    one dialogue settlement queue, so the only active entries are the extension
    popup and desktop web. Mobile web previously had its own 准/不准 buttons
    wired straight to the deprecated `POST /api/insights/feedback`, bypassing
    the anchor/queue path entirely. Nothing guarded the removal, so a future
    edit could silently reintroduce a second settlement entry point.
    """

    def _profile_js(self) -> str:
        return (ROOT / "src/openbiliclaw/web/js/views/profile.js").read_text(encoding="utf-8")

    def test_mobile_insights_have_no_settlement_buttons(self) -> None:
        profile_js = self._profile_js()
        after_marker = profile_js.split("// Active insights", 1)[1]
        insight_block = after_marker.split("// Recent awareness", 1)[0]
        assert 'data-action="confirm"' not in insight_block
        assert 'data-action="reject"' not in insight_block
        assert "insight-actions" not in insight_block

    def test_mobile_web_does_not_call_the_legacy_feedback_endpoint(self) -> None:
        profile_js = self._profile_js()
        assert "submitInsightFeedback" not in profile_js, (
            "mobile web must not reach the deprecated settlement endpoint"
        )
        assert "bindInsightActions" not in profile_js

    def test_mobile_insights_point_users_at_the_real_entry(self) -> None:
        """Read-only is only honest if the user is told where to act."""
        profile_js = self._profile_js()
        assert "insight-readonly" in profile_js
        assert "请在「聊聊口味」的待聊确认入口处理" in profile_js

    def test_mobile_web_wires_the_shared_card_action_helper(self) -> None:
        """The mobile dialogue is a first-class settlement surface."""
        mobile_dir = ROOT / "src/openbiliclaw/web/js"
        consumers = [
            path.relative_to(ROOT)
            for path in mobile_dir.rglob("*.js")
            if "executeCardAction" in path.read_text(encoding="utf-8")
        ]
        assert consumers == [Path("src/openbiliclaw/web/js/views/chat.js")]
