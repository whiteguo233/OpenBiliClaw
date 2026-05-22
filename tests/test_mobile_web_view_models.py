"""Regression tests for mobile web view-model normalization helpers."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

_NODE = shutil.which("node")


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
              "buildVideoUrl", "buildContentUrl",
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
              "getRecommendationCoverPreloadUrls", "getRecommendationImageLoadingAttrs",
              "formatRelativeTimestamp",
              "normalizeSourcePlatform", "getSourceLabel",
              "normalizeCoverUrl", "getCoverImageAttrs",
              "normalizePoolStatus", "normalizeMbtiDimensions", "normalizeChatTurn",
            ];
            for (const name of required) {
                assert.equal(typeof vm[name], "function", `missing export: ${name}`);
            }
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
        assert "message-cover-frame" in chat_js
        assert "card-cover-frame" in app_css
        assert ".card-cover-frame.is-error" in app_css
        assert ".message-cover-frame.is-error" in app_css
        assert ".card-cover::after" not in app_css
        assert ".message-cover-frame img" in app_css

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
        assert "await warmRecommendationCovers(newItems," in recommend_js
        assert "waitForDecode: true" in recommend_js
        assert 'loading="${esc(imageAttrs.loading)}"' in recommend_js
        assert 'fetchpriority="${esc(imageAttrs.fetchPriority)}"' in recommend_js
        assert "AUTO_APPEND_ROOT_MARGIN" in recommend_js
        assert "IntersectionObserver" in recommend_js
        assert "observeAutoAppendSentinel" in recommend_js
        assert ".load-more-row" in recommend_js
        assert "handleAppend();" in recommend_js

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
            assert.equal(view.permanent, true);

            const reject = getDelightActionState("reject");
            assert.equal(reject.apiResponse, "dislike");
            assert.equal(reject.uiState, "rejected");
            assert.equal(reject.permanent, true);

            const like = getDelightActionState("like");
            assert.equal(like.apiResponse, "like");
            assert.equal(like.uiState, "liked");
            assert.equal(like.permanent, true);

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
            assert.equal(pending.visible, true);
            assert.equal(pending.handled, false);
            assert.equal(pending.score_label, "大概率会戳中你");

            const viewed = getDelightUiState({ bvid: "BV1", state: "viewed", delight_score: 0.7 });
            assert.equal(viewed.handled, true);
            assert.equal(viewed.response_tone, "success");

            const liked = getDelightUiState({ bvid: "BV1", state: "liked", delight_score: 0.7 });
            assert.equal(liked.handled, true);
            assert.equal(liked.response_tone, "success");

            const chatted = getDelightUiState({
                bvid: "BV1",
                state: "chatted",
                delight_score: 0.7,
            });
            assert.equal(chatted.handled, false);
            assert.equal(chatted.response_tone, "info");

            const empty = getDelightUiState({});
            assert.equal(empty.visible, false);
        """)
        )

    def test_chat_alignment_helpers(self) -> None:
        _assert_js(
            dedent("""
            import assert from "node:assert/strict";
            import {
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
                ["喜欢", "confirm"],
                ["不喜欢", "reject"],
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
                ["最近补进", "补进 7 条", "brand"],
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
                ["最近补进", "补进 1 条"],
                ["现在在忙", "小红书任务 / 探索"],
              ],
            );
        """)
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
              speculative_interests: [{ domain: "cooking", confidence: 0.6, status: "active" }],
            });
            assert.equal(full.initialized, true);
            assert.equal(full.personality_portrait, "test portrait");
            assert.deepEqual(full.core_traits, ["curious"]);
            assert.equal(full.likes.length, 1);
            assert.equal(full.likes[0].specifics[0].name, "AI");
            assert.equal(full.exploration_openness, 0.7);
            assert.deepEqual(full.favorite_up_users, ["UP1"]);
            assert.equal(full.speculative_interests[0].domain, "cooking");
        """)
        )

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
