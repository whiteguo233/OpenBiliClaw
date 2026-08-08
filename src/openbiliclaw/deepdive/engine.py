"""深潜研究 · 核心引擎：搜索计划生成 + 搜索执行 + 结果聚合"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from openbiliclaw.deepdive.session import DeepDiveCard, DeepDiveSession, SearchPlan, SessionManager

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import ContentDiscoveryEngine
    from openbiliclaw.llm.service import StructuredTaskService
    from openbiliclaw.soul.profile import SoulProfile

logger = logging.getLogger(__name__)

# ── LLM Prompt 模板 ──────────────────────────────────────────

DEEP_DIVE_PLAN_PROMPT_TEMPLATE = """你是一个深潜研究助手。用户想深入了解某个领域，请帮他们拟定搜索计划。

## 用户输入
{topic}

## 用户画像（可供参考）
{profile_summary}

## 任务
1. 根据用户输入，生成**澄清问题**（最多 3 个），用来帮助用户细化需求
2. 根据用户输入，生成**多平台搜索关键词**——只在以下**已启用平台**中选择：
{enabled_platforms}

## 输出格式
请严格输出 JSON（不要 markdown 包裹），格式如下：
{{
    "topic": "总结后的主题",
    "clarifying_questions": ["问题1", "问题2", "问题3"],
    "keywords": [
        {{"platform": "bilibili", "query": "搜索词1", "type": "video"}},
        {{"platform": "xhs", "query": "搜索词2", "type": "note"}},
        {{"platform": "zhihu", "query": "搜索词3", "type": "article"}}
    ]
}}

注意：platform 只能从上述已启用平台中选择，不要生成未启用平台的搜索词
type 可选值：video, note, article, general
"""

DEEP_DIVE_EXECUTE_PROMPT_TEMPLATE = """你是一个深潜研究助手。用户已经确认了搜索计划，请生成搜索关键词来执行。

## 搜索主题
{topic}

## 用户补充信息
{clarification}

## 已启用平台（只能从中选择）
{enabled_platforms}

## 输出格式
请严格输出 JSON（不要 markdown 包裹），格式如下：
{{
    "keywords": [
        {{"platform": "bilibili", "query": "搜索词1", "type": "video"}},
        {{"platform": "xhs", "query": "搜索词2", "type": "note"}}
    ]
}}
"""

DEEP_DIVE_REFINE_PROMPT_TEMPLATE = """用户对当前搜索结果不满意，希望调整方向。

## 当前搜索主题
{topic}

## 用户修正意见
{refinement}

## 已启用平台（只能从中选择）
{enabled_platforms}

## 请基于修正意见，生成新的搜索关键词
输出格式：
{{
    "keywords": [
        {{"platform": "bilibili", "query": "搜索词1", "type": "video"}},
        {{"platform": "xhs", "query": "搜索词2", "type": "note"}}
    ]
}}
"""


class DeepDiveEngine:
    """深潜研究引擎"""

    def __init__(
        self,
        session_manager: SessionManager,
        llm_service: Any,  # StructuredTaskService
        discovery_engine: Any,  # ContentDiscoveryEngine
        bilibili_client: Any = None,  # BilibiliClient
        enabled_platforms: list[str] | None = None,  # 已启用平台白名单
        data_path: str | None = None,  # 数据目录（抖音 cookie 读取用）
        douyin_search_fn: Any = None,  # 抖音搜索回调（plugin 机制，规避风控）
        xhs_search_fn: Any = None,  # 小红书搜索回调（扩展任务队列，返回带封面笔记）
        prompts: dict[str, str] | None = None,  # 自定义 prompt（plan/execute/refine），缺省用内置默认
    ):
        self.session_manager = session_manager
        self.llm_service = llm_service
        self.discovery_engine = discovery_engine
        self.bilibili_client = bilibili_client
        self.enabled_platforms = list(enabled_platforms or [])
        self.data_path = data_path
        self.douyin_search_fn = douyin_search_fn
        self.xhs_search_fn = xhs_search_fn
        # 生效的 prompt 模板：自定义优先，否则内置默认
        self.prompts = {
            "plan": (prompts or {}).get("plan") or DEEP_DIVE_PLAN_PROMPT_TEMPLATE,
            "execute": (prompts or {}).get("execute") or DEEP_DIVE_EXECUTE_PROMPT_TEMPLATE,
            "refine": (prompts or {}).get("refine") or DEEP_DIVE_REFINE_PROMPT_TEMPLATE,
        }
        self._profile: Any = None

    def _platform_hint(self) -> str:
        """把已启用平台转成 prompt 提示文本"""
        names = {
            "bilibili": "bilibili（B站，视频）",
            "xhs": "xhs（小红书，笔记）",
            "zhihu": "zhihu（知乎，文章）",
            "youtube": "youtube（YouTube，视频）",
            "douyin": "douyin（抖音，短视频）",
            "reddit": "reddit（Reddit，帖子）",
        }
        if not self.enabled_platforms:
            return "bilibili（B站）"
        return "、".join(names.get(p, p) for p in self.enabled_platforms)

    async def start_session(self, topic: str, profile: Any = None) -> dict:
        """开始新会话：创建 session + 生成澄清问题 + 搜索计划"""
        self._profile = profile
        session = self.session_manager.create_session(topic=topic)
        self.session_manager.set_status(session.id, "clarifying")

        # 生成探索计划
        plan = await self._generate_plan(topic, profile)
        self.session_manager.set_plan(session.id, plan)

        # 把澄清问题添加到消息列表
        for q in plan.clarifying_questions:
            self.session_manager.add_message(session.id, "assistant", q)

        return self._session_to_dict(session)

    async def clarify(self, session_id: str, user_input: str) -> dict:
        """用户回答了澄清问题，生成搜索关键词并执行"""
        session = self.session_manager.get_session(session_id)
        if not session:
            return {"error": "会话不存在", "session_id": session_id}

        self.session_manager.add_message(session_id, "user", user_input)
        self.session_manager.set_status(session_id, "searching")

        plan = session.search_plan
        if not plan:
            return {"error": "没有搜索计划", "session_id": session_id}

        # 根据用户澄清生成具体搜索关键词
        keywords = await self._generate_keywords(plan.topic, user_input)
        if not keywords:
            keywords = plan.keywords

        # 执行搜索
        cards = await self._execute_search(keywords, session)
        self.session_manager.set_results(session_id, cards)

        return self._session_to_dict(session)

    async def refine(self, session_id: str, refinement: str) -> dict:
        """用户修正搜索方向，重新搜索"""
        session = self.session_manager.get_session(session_id)
        if not session:
            return {"error": "会话不存在", "session_id": session_id}

        self.session_manager.add_message(session_id, "user", refinement)
        self.session_manager.set_status(session_id, "searching")

        # 根据修正重新生成关键词
        plan = session.search_plan
        if not plan:
            return {"error": "没有搜索计划", "session_id": session_id}

        keywords = await self._generate_refined_keywords(plan.topic, refinement)
        if not keywords:
            # 回退：直接用修正文本作为关键词
            keywords = [
                {"platform": "bilibili", "query": refinement, "type": "video"},
                {"platform": "xhs", "query": refinement, "type": "note"},
            ]

        # 执行搜索
        cards = await self._execute_search(keywords, session)
        self.session_manager.set_results(session_id, cards)

        return self._session_to_dict(session)

    async def _generate_plan(self, topic: str, profile: Any = None) -> SearchPlan:
        """LLM 生成探索计划"""
        profile_summary = ""
        if profile:
            try:
                if hasattr(profile, "compact_summary"):
                    profile_summary = profile.compact_summary()
                else:
                    profile_summary = str(profile)[:500]
            except Exception:
                profile_summary = ""

        prompt = self.prompts["plan"].format(
            topic=topic,
            profile_summary=profile_summary or "（无可用画像）",
            enabled_platforms=self._platform_hint(),
        )

        try:
            result = await self.llm_service.complete_structured_task(
                system_instruction="你是一个深潜研究助手，输出 JSON。",
                user_input=prompt,
                caller="deepdive.plan",
            )
            data = self._parse_json_response(getattr(result, "content", result))
            return SearchPlan(
                topic=data.get("topic", topic),
                keywords=data.get("keywords", []),
                clarifying_questions=data.get("clarifying_questions", []),
                raw_prompt=prompt,
            )
        except Exception as e:
            logger.warning("LLM 生成探索计划失败: %s，使用默认计划", e)
            return SearchPlan(
                topic=topic,
                keywords=[
                    {"platform": "bilibili", "query": topic, "type": "video"},
                    {"platform": "xhs", "query": topic, "type": "note"},
                ],
                clarifying_questions=["你想深入了解哪个方面？", "有什么具体需求吗？"],
            )

    async def _generate_keywords(self, topic: str, clarification: str) -> list[dict]:
        """LLM 根据用户澄清生成搜索关键词"""
        prompt = self.prompts["execute"].format(
            topic=topic,
            clarification=clarification,
            enabled_platforms=self._platform_hint(),
        )
        try:
            result = await self.llm_service.complete_structured_task(
                system_instruction="你是一个深潜研究助手，输出 JSON。",
                user_input=prompt,
                caller="deepdive.keywords",
            )
            data = self._parse_json_response(getattr(result, "content", result))
            return data.get("keywords", [])
        except Exception as e:
            logger.warning("LLM 生成搜索关键词失败: %s", e)
            return []

    async def _generate_refined_keywords(self, topic: str, refinement: str) -> list[dict]:
        """LLM 根据用户修正重新生成搜索关键词"""
        prompt = self.prompts["refine"].format(
            topic=topic,
            refinement=refinement,
            enabled_platforms=self._platform_hint(),
        )
        try:
            result = await self.llm_service.complete_structured_task(
                system_instruction="你是一个深潜研究助手，输出 JSON。",
                user_input=prompt,
                caller="deepdive.refine",
            )
            data = self._parse_json_response(getattr(result, "content", result))
            return data.get("keywords", [])
        except Exception as e:
            logger.warning("LLM 生成修正搜索词失败: %s", e)
            return []

    async def _execute_search(self, keywords: list[dict], session: DeepDiveSession) -> list[DeepDiveCard]:
        """执行搜索：对每个关键词调用现有搜索策略，聚合去重 + 平台混合排序"""
        all_cards: list[DeepDiveCard] = []

        for kw in keywords:
            platform = kw.get("platform", "bilibili")
            query = kw.get("query", "")
            if not query:
                continue

            try:
                cards = await self._search_platform(platform, query)
                all_cards.extend(cards)
            except Exception as e:
                logger.warning("平台 %s 搜索 '%s' 失败: %s", platform, query, e)

        # ── 去重（复用主站 discovery/engine 机制） ──────────────
        # 1. 身份去重：platform:content_id 优先（同一内容多关键词/多平台搜到不重复）
        # 2. 标题归一化去重：跨平台近似重复内容（复用 _normalize_prompt_text_for_dedupe 去空白）
        try:
            from openbiliclaw.discovery.engine import _normalize_prompt_text_for_dedupe
        except Exception:
            _normalize_prompt_text_for_dedupe = lambda v: re.sub(r"\s+", "", v).strip()

        by_identity: dict[str, DeepDiveCard] = {}
        by_title: dict[str, DeepDiveCard] = {}
        for card in all_cards:
            # 身份 key：优先内容 ID，其次 url，最后 title+author
            cid = (card.bvid or "").strip()
            identity = f"{card.platform}:{cid}" if cid else (card.url or f"{card.platform}:title:{card.title}:{card.author or ''}")
            existing = by_identity.get(identity)
            if existing is None or card.score > existing.score:
                by_identity[identity] = card
            # 标题归一化 key（跨平台去重）：去空白后的标题
            title_key = _normalize_prompt_text_for_dedupe(card.title)
            if title_key:
                existing_t = by_title.get(title_key)
                if existing_t is None or card.score > existing_t.score:
                    by_title[title_key] = card

        merged = list(by_identity.values())
        # 用标题归一化去重覆盖（保留高分，同标题只留一条）
        seen_titles: set[str] = set()
        final_cards: list[DeepDiveCard] = []
        for card in sorted(merged, key=lambda c: c.score, reverse=True):
            tk = _normalize_prompt_text_for_dedupe(card.title)
            if tk and tk in seen_titles:
                continue
            if tk:
                seen_titles.add(tk)
            final_cards.append(card)

        # ── 平台混合排序（复用主站 _merge_and_rank 思路） ────────
        # 平台权重：B站/抖音/小红书 真实结果优先，链接卡兜底靠后
        platform_weight = {"bilibili": 3, "douyin": 2, "xhs": 2, "zhihu": 1, "youtube": 1}

        def _is_link_card(c: DeepDiveCard) -> bool:
            # 链接卡：无内容 ID 且标题形如「X平台搜索: query」
            return (not c.bvid) and (":搜索:" in c.title or "搜索:" in c.title)

        def _sort_key(c: DeepDiveCard) -> tuple:
            return (
                _is_link_card(c),          # 链接卡排最后
                -platform_weight.get(c.platform, 1),  # 平台权重
                -c.score,                   # 原始分
                c.title or "",
            )

        final_cards.sort(key=_sort_key)
        return final_cards[:30]  # 最多返回 30 条

    async def _search_platform(self, platform: str, query: str) -> list[DeepDiveCard]:
        """搜索单个平台"""
        cards: list[DeepDiveCard] = []

        def _abs(url: str) -> str:
            """平台返回协议相对路径（//i0.hdslb.com/...），补全 https:"""
            url = (url or "").strip()
            if url.startswith("//"):
                url = "https:" + url
            return url

        if platform == "bilibili" and self.bilibili_client is not None:
            try:
                results = await self.bilibili_client.search(query, page=1, page_size=10, order="totalrank")
                for r in (results or []):
                    bvid = r.get("bvid", "")
                    cards.append(DeepDiveCard(
                        platform="bilibili",
                        title=(r.get("title", "") or "").strip(),
                        url=f"https://www.bilibili.com/video/{bvid}" if bvid else "",
                        description=r.get("desc", "") or "",
                        author=r.get("author", "") or "",
                        cover_url=_abs(r.get("pic", "")),
                        published_at=str(r.get("pubdate", "") or ""),
                        score=float(r.get("score", 0) or 0),
                        bvid=bvid,
                    ))
            except Exception as e:
                logger.warning("B站搜索 '%s' 失败: %s", query, e)
            if not cards:
                cards.append(DeepDiveCard(
                    platform="bilibili",
                    title=f"B站搜索: {query}",
                    url=f"https://search.bilibili.com/all?keyword={query}",
                    score=0.5,
                ))

        elif platform == "douyin":
            # 抖音搜索：优先 plugin 机制（浏览器扩展模拟真实操作，规避 direct-cookie 风控）
            dy_items: list[dict] = []
            if self.douyin_search_fn is not None:
                try:
                    dy_items = await self.douyin_search_fn(query) or []
                except Exception as e:
                    logger.warning("抖音 plugin 搜索 '%s' 失败: %s", query, e)
            if not dy_items:
                # 兜底：direct-cookie 搜索
                try:
                    from openbiliclaw.sources.douyin_direct import DouyinDirectClient
                    from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
                    from pathlib import Path
                    cookie = resolve_douyin_cookie(
                        data_dir=Path(self.data_path),
                        cookie_env="OPENBILICLAW_DOUYIN_COOKIE",
                    )
                    if cookie:
                        async with DouyinDirectClient(cookie=cookie) as dy_client:
                            dy_items = await dy_client.search_aweme(query, limit=10)
                except Exception as e:
                    logger.warning("抖音 direct 搜索 '%s' 失败: %s", query, e)
            for r in (dy_items or []):
                aweme_id = r.get("aweme_id") or r.get("awemeId") or ""
                title = (r.get("desc") or r.get("title") or "").strip()
                if not title:
                    continue
                author = r.get("author") or {}
                author_name = author.get("nickname") if isinstance(author, dict) else str(author or "")
                video = r.get("video") or {}
                cover = r.get("cover") or (video.get("cover") if isinstance(video, dict) else "") or ""
                # plugin 返回的 cover 可能是 {"url_list": [...]} 结构，取第一个真实 URL
                if isinstance(cover, dict):
                    url_list = cover.get("url_list") or cover.get("urlList") or []
                    cover = url_list[0] if url_list else ""
                elif isinstance(cover, str) and cover.strip().startswith("{") and "url_list" in cover:
                    # 已字符串化的 dict（如 "{'url_list': [...]}"），尝试解析
                    try:
                        import ast
                        parsed = ast.literal_eval(cover)
                        if isinstance(parsed, dict):
                            url_list = parsed.get("url_list") or parsed.get("urlList") or []
                            cover = url_list[0] if url_list else ""
                    except Exception:
                        pass
                cards.append(DeepDiveCard(
                    platform="douyin",
                    title=title,
                    url=f"https://www.douyin.com/video/{aweme_id}" if aweme_id else "",
                    description=title,
                    author=author_name,
                    cover_url=_abs(str(cover)),
                    score=1.0,
                    bvid=aweme_id,
                ))
            if not cards:
                cards.append(DeepDiveCard(
                    platform="douyin",
                    title=f"抖音搜索: {query}",
                    url=f"https://www.douyin.com/search/{query}",
                    score=0.4,
                    description="",
                ))

        elif platform == "xhs":
            # 小红书搜索：优先扩展任务队列（XhsTaskQueue + 浏览器扩展真实执行，返回带封面笔记）
            if self.xhs_search_fn is not None:
                try:
                    xhs_items = await self.xhs_search_fn(query) or []
                except Exception as e:
                    logger.warning("小红书 plugin 搜索 '%s' 失败: %s", query, e)
                    xhs_items = []
                for r in xhs_items:
                    title = (r.get("title") or "").strip()
                    if not title:
                        continue
                    cards.append(DeepDiveCard(
                        platform="xhs",
                        title=title,
                        url=_abs(r.get("url", "")),
                        description=(r.get("desc") or r.get("description") or "") or "",
                        author=r.get("author") or "",
                        cover_url=_abs(r.get("cover_url", "")),
                        score=float(r.get("score", 0.8) or 0.8),
                        bvid=r.get("note_id") or r.get("content_id") or "",
                    ))
                if cards:
                    return cards
            # 兜底：链接卡
            cards.append(DeepDiveCard(
                platform="xhs",
                title=f"小红书搜索: {query}",
                url=f"https://www.xiaohongshu.com/search_result?keyword={query}",
                score=0.4,
                description="",
            ))

        elif platform == "zhihu":
            cards.append(DeepDiveCard(
                platform="zhihu",
                title=f"知乎搜索: {query}",
                url=f"https://www.zhihu.com/search?type=content&q={query}",
                score=0.4,
                description="",
            ))

        elif platform == "youtube":
            cards.append(DeepDiveCard(
                platform="youtube",
                title=f"YouTube搜索: {query}",
                url=f"https://www.youtube.com/results?search_query={query}",
                score=0.4,
                description="",
            ))

        return cards

    def _parse_json_response(self, result: Any) -> dict:
        """解析 LLM 返回的 JSON"""
        if isinstance(result, dict):
            return result
        text = str(result)
        # 尝试去掉 markdown 包裹
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0]
        elif "```" in text:
            text = text.split("```")[1].split("```")[0]
        return json.loads(text.strip())

    def _session_to_dict(self, session: DeepDiveSession) -> dict:
        """将会话转换为字典（API 返回用）"""
        return {
            "id": session.id,
            "topic": session.topic,
            "status": session.status,
            "folder": session.folder or "",
            "messages": [
                {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                for m in session.messages
            ],
            "search_plan": {
                "topic": session.search_plan.topic if session.search_plan else "",
                "keywords": session.search_plan.keywords if session.search_plan else [],
                "clarifying_questions": session.search_plan.clarifying_questions if session.search_plan else [],
                "raw_prompt": session.search_plan.raw_prompt if session.search_plan else "",
            } if session.search_plan else None,
            "results": [
                {
                    "platform": c.platform,
                    "title": c.title,
                    "url": c.url,
                    "description": c.description,
                    "author": c.author,
                    "cover_url": c.cover_url,
                    "published_at": c.published_at,
                    "score": c.score,
                    "bvid": c.bvid,
                }
                for c in session.results
            ],
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }