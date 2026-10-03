"""One credential write path, one validation strength.

Before this module the API spelled "store a credential" five different ways
(spec D5) and two of them disagreed about how hard to check the *same* B站
cookie: ``POST /api/bilibili/cookie`` ran a live nav probe and refused to
persist a rejected cookie, while ``PUT /api/config`` — the route the settings
page's paste box actually uses — wrote it straight to ``config.toml`` with no
check at all (spec D4). A user pasting a dead cookie was told "saved".

Everything here exists so that cannot happen again:

* :func:`validate_credential` is the single gate. Both write surfaces call it
  with the same arguments, so "which endpoint did you use" can no longer change
  the verdict (invariant I5).
* :data:`CREDENTIAL_SPECS` states, per platform, exactly how far a write-time
  check can get. Where it cannot reach a verdict at save time — X deliberately
  keeps extension sync structural-only, while 小红书/知乎 store a bare
  boolean the backend can never audit — the spec carries the *reason*, and the
  response says so out loud rather than returning a bare success (invariant I3).

**Rejection requires evidence.** A structural failure is evidence (a B站 jar
without ``DedeUserID`` cannot log anyone in). An explicit platform "not logged
in" is evidence. A transport failure is *not* evidence about the credential —
but it is still a refusal to persist on the two platforms we can normally
confirm, because a credential we were unable to check is exactly the thing this
module exists to stop from landing silently. That refusal is reported as
``validation_network``, the code the browser extension already backs off on.

**What is not validated is named.** ``PUT /api/config`` keeps its own
partial-update semantics — an empty or masked-echo field means "this field was
not edited", decided *before* validation is reached — so the two paths still
share one and only one notion of what makes a credential invalid.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from openbiliclaw.api.source_auth.probe_cache import (
    LIVE_PROBES,
    PROBE_OK_TTL_SECONDS,
    LiveProbeCache,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.config import Config

logger = logging.getLogger(__name__)

#: What a caller claims it is storing. ``token`` is deliberately separate from
#: ``cookie``: 小红书's ``xsec_token`` is a per-note *content access* token, not
#: a login credential (spec D5), and collapsing the two would let a token write
#: inherit a login credential's validation promises.
CredentialKind = Literal["cookie", "token", "login_state"]

#: How far the write-time check actually got. ``none`` is a legitimate answer
#: and must be surfaced, never smoothed into a silent success (invariant I3).
CheckDepth = Literal["live_probe", "structural", "none"]

#: Which input affordance a surface may render for a platform. This is a
#: statement about *write capability*, not about layout: ``extension_only``
#: means the backend stores no pasteable credential for this platform at all,
#: so a text box would be a lie no matter how nicely it is styled. ``none``
#: means the platform takes no credential in the first place.
FormKind = Literal["cookie_textarea", "token_input", "extension_only", "none"]


@dataclass(frozen=True)
class CredentialSpec:
    """What one platform accepts, how strongly a write can be checked, and how
    a settings page should ask for it.

    ``required_keys`` / ``any_of_keys`` are the structural gate. They are cookie
    *names*, chosen to match what the corresponding provider in
    ``providers.py`` already counts, so a value the write path accepts can never
    render as ``credential="invalid"`` a second later on the status chip.

    The ``form_*`` fields live here, on the same row as the gate they describe,
    for one reason: a form that advertises different required keys than the gate
    enforces is the D6 drift rebuilt in a new place. ``forms.build_credential_form``
    *derives* the descriptor from these fields rather than restating them, so
    "which names must be present" has exactly one definition per platform.
    """

    slug: str
    kinds: tuple[CredentialKind, ...] = ()
    required_keys: tuple[str, ...] = ()
    any_of_keys: tuple[str, ...] = ()
    #: The credential is one opaque secret (a bearer token), not a ``name=value``
    #: cookie jar, so its identity fingerprint covers the whole value rather than
    #: named keys. Bangumi's personal access token is the only one today. Kept
    #: separate from ``required_keys`` because the structural gate has nothing to
    #: count here — validity is a live ``/v0/me`` question, not a field-presence
    #: one — while the probe cache still needs a stable per-credential digest.
    opaque_credential: bool = False
    invalid_error_code: str = "cookie_invalid"
    invalid_message: str = ""
    #: Whether a live probe runs before persisting. True only where a control
    #: experiment established a clean logged-in / logged-out discriminator.
    live_gate: bool = False
    #: I5: why a write here cannot be confirmed. MUST be non-empty exactly when
    #: ``live_gate`` is False and the platform accepts a credential at all.
    unverified_reason: str = ""

    # ── settings-page presentation (spec Phase 4) ────────────────────────
    #: MUST be ``none`` when ``kinds`` is empty and MUST NOT be a writable kind
    #: when the backend stores no pasteable credential. Asserted in
    #: ``test_form_kind_matches_actual_write_capability``.
    form_kind: FormKind = "none"
    form_label: str = ""
    form_placeholder: str = ""
    #: Dotted config path holding the *name* of the env var this platform
    #: honours, plus the fallback used when the config predates the field.
    #: Empty means no env-var override exists — B站 reads config.toml and the
    #: data file only, and claiming otherwise would send users editing a
    #: variable nothing reads.
    env_var_path: str = ""
    env_var_default: str = ""
    #: Where a user goes to (re)establish a login. Empty for platforms with no
    #: login at all; this drives the ``open_login_window`` action, which is the
    #: only actionable affordance ``extension_only`` platforms have.
    login_url: str = ""
    help_text: str = ""


CREDENTIAL_SPECS: dict[str, CredentialSpec] = {
    "bilibili": CredentialSpec(
        slug="bilibili",
        kinds=("cookie",),
        # The same three names ``auth_bilibili`` counts. A jar missing any of
        # them cannot authenticate, so this is a verdict, not a guess.
        required_keys=("SESSDATA", "bili_jct", "DedeUserID"),
        invalid_message=(
            "B站 Cookie 不完整（缺少 SESSDATA / bili_jct / DedeUserID），未保存。"
            "请在已登录的浏览器重新复制完整 Cookie。"
        ),
        live_gate=True,
        form_kind="cookie_textarea",
        form_label="B 站 Cookie",
        form_placeholder="保留空值表示不覆盖现有 cookie",
        login_url="https://www.bilibili.com/",
        help_text=(
            "在已登录的浏览器打开 B 站，复制完整 Cookie 粘贴到这里。保存时会真的向 B 站验证一次，"
            "验证不通过不会落盘。"
        ),
    ),
    "douyin": CredentialSpec(
        slug="douyin",
        kinds=("cookie",),
        # A guest 抖音 jar carries ttwid / odin_tt but none of these; the
        # strip-down control experiment in spec D11 removed exactly this family
        # to produce its logged-out group.
        any_of_keys=("sessionid", "sessionid_ss", "sid_tt"),
        invalid_message=(
            "抖音 Cookie 缺少登录态字段（sessionid / sessionid_ss / sid_tt），未保存。"
            "请在已登录的浏览器重新复制完整 Cookie。"
        ),
        live_gate=True,
        form_kind="cookie_textarea",
        form_label="抖音 Cookie",
        form_placeholder="保留空值表示不覆盖现有 cookie（登录抖音后插件会自动同步）",
        env_var_path="sources.douyin.cookie_env",
        env_var_default="OPENBILICLAW_DOUYIN_COOKIE",
        login_url="https://www.douyin.com/",
        help_text=(
            "登录抖音后插件会自动同步，一般不需要手动粘贴。保存时会真的向抖音验证一次，"
            "验证不通过不会落盘。"
        ),
    ),
    "twitter": CredentialSpec(
        slug="twitter",
        kinds=("cookie",),
        required_keys=("auth_token", "ct0"),
        invalid_error_code="missing_x_cookies",
        invalid_message=(
            "X Cookie 缺少 auth_token / ct0，未保存 —— twitter-cli 没有这两项会直接 401。"
        ),
        unverified_reason=(
            "保存时只做 auth_token / ct0 结构校验；"
            "可点击‘测试连接’使用只读账户状态请求确认真实登录态。"
        ),
        form_kind="cookie_textarea",
        form_label="X Cookie",
        form_placeholder="保留空值表示不覆盖现有 cookie（登录 x.com 后插件会自动同步）",
        env_var_path="sources.twitter.cookie_env",
        env_var_default="OPENBILICLAW_X_COOKIE",
        login_url="https://x.com/",
        help_text=(
            "登录 x.com 后插件会自动同步。保存时只检查 auth_token / ct0 是否齐全，"
            "点击‘测试连接’即可用只读请求确认登录态。"
        ),
    ),
    "reddit": CredentialSpec(
        slug="reddit",
        kinds=("cookie",),
        required_keys=("reddit_session",),
        invalid_error_code="missing_reddit_session",
        invalid_message=(
            "Reddit Cookie 未保存：缺少 reddit_session，"
            "请从已登录 reddit.com 的浏览器复制完整 Cookie。"
        ),
        unverified_reason=(
            "Reddit 凭据写入 rdt-cli 的本地凭据库，保存时只做结构校验，不联网验证。"
        ),
        form_kind="cookie_textarea",
        form_label="Reddit Cookie",
        form_placeholder="保留空值表示不覆盖现有 cookie（登录 reddit.com 后插件会自动同步）",
        login_url="https://www.reddit.com/",
        help_text=(
            "凭据写入 rdt-cli 的本地凭据库，不存在 config.toml 里。保存时只检查 reddit_session "
            "是否存在，不联网验证。"
        ),
    ),
    "xiaohongshu": CredentialSpec(
        slug="xiaohongshu",
        kinds=("login_state", "token"),
        unverified_reason=(
            "小红书只上报一个登录布尔值，后端一个字节的 Cookie 都不保存，"
            "架构上无从校验；结论完全取决于浏览器插件上报的内容。"
        ),
        # Not ``token_input`` even though ``kinds`` accepts ``token``: the
        # xsec_token values are harvested by the extension while the user
        # browses, never typed. A paste box here would invite users to hand-fix
        # a login problem with a value that is not a login credential.
        form_kind="extension_only",
        form_label="小红书登录态",
        login_url="https://www.xiaohongshu.com/",
        help_text=(
            "后端不保存小红书 Cookie，登录态由浏览器插件上报。要修就去浏览器登录小红书，"
            "插件会自动上报；这里没有可粘贴的凭据。"
        ),
    ),
    "zhihu": CredentialSpec(
        slug="zhihu",
        kinds=("login_state",),
        unverified_reason=(
            "知乎只上报一个登录布尔值，后端不保存 Cookie，架构上无从校验；"
            "结论完全取决于浏览器插件上报的内容。"
        ),
        form_kind="extension_only",
        form_label="知乎登录态",
        login_url="https://www.zhihu.com/",
        help_text=(
            "后端不保存知乎 Cookie，登录态由浏览器插件上报。要修就去浏览器登录知乎，"
            "插件会自动上报；这里没有可粘贴的凭据。"
        ),
    ),
    "youtube": CredentialSpec(
        slug="youtube",
        kinds=(),
        unverified_reason="YouTube 按公开源接入，不需要也不接受任何凭据。",
        form_kind="none",
        form_label="YouTube",
        help_text="YouTube 按公开源接入，不需要登录，也没有任何凭据要填。",
    ),
    "tiktok": CredentialSpec(
        slug="tiktok",
        kinds=("cookie",),
        # A guest TikTok jar carries ttwid / msToken but none of these; they
        # are the same ByteDance session-cookie family 抖音's gate counts.
        any_of_keys=("sessionid", "sessionid_ss", "sid_tt"),
        invalid_message=(
            "TikTok Cookie 缺少登录态字段（sessionid / sessionid_ss / sid_tt），未保存。"
            "请在已登录的浏览器重新复制完整 Cookie。"
        ),
        live_gate=True,
        form_kind="cookie_textarea",
        form_label="TikTok Cookie（可选）",
        form_placeholder="留空表示不覆盖现有 cookie（访客身份下无需配置）",
        env_var_path="sources.tiktok.cookie_env",
        env_var_default="OPENBILICLAW_TIKTOK_COOKIE",
        login_url="https://www.tiktok.com/",
        help_text=(
            "TikTok 默认以访客身份读公开数据，无需登录；登录 Cookie 只用于解锁关键词搜索"
            "与更高限额（登录态抓取有账号风险，见 TikTok 来源文档）。保存时会真的向 "
            "TikTok 验证一次（passport 会话心跳），验证不通过不会落盘。"
        ),
    ),
    "bangumi": CredentialSpec(
        slug="bangumi",
        # ``kinds=()``: the unified write endpoint does not accept Bangumi's
        # token. It is anonymous-optional, written through the config / init form
        # (``sources.bangumi.access_token``) which validates it structurally and,
        # from the settings save path, live via ``/v0/me``. Routing it through
        # here too would need a distinct token kind and a config-mirror writer,
        # duplicating a path that already exists — so this stays a config-only
        # credential and the form offers a verify button + a "去获取令牌" link
        # rather than a paste box that would write nowhere.
        kinds=(),
        # The token has no field structure; its whole value is its identity, so
        # the probe cache can tell one token from another (pitfall #2 in spirit:
        # a verdict is about a credential, not a platform).
        opaque_credential=True,
        unverified_reason=(
            "Bangumi 公开发现无需凭据；个人令牌是可选项，在设置 / 初始化里填写"
            "（保存时会用 /v0/me 校验），这里不单独接收令牌写入。"
        ),
        form_kind="none",
        form_label="Bangumi 个人令牌（可选）",
        login_url="https://next.bgm.tv/demo/access-token",
        help_text=(
            "Bangumi 公开收藏 / 排行匿名即可发现，无需登录。可选个人令牌用于识别当前账号、"
            "读取私密收藏：点「去获取令牌」生成后，在初始化 / 设置里填写，保存时会真的向 "
            "Bangumi 校验一次。填好后可点「测试连接」用 /v0/me 复验。"
        ),
    ),
    "github": CredentialSpec(
        slug="github",
        # GitHub PATs are config-owned. The unified credential endpoint must
        # not grow a second secret writer with different validation semantics.
        kinds=(),
        opaque_credential=True,
        unverified_reason=(
            "GitHub 公开仓库发现无需凭据；可选 PAT 仅在设置 / config.toml 的 "
            "[sources.github] 或 OPENBILICLAW_GITHUB_TOKEN 中配置。"
        ),
        form_kind="none",
        form_label="GitHub PAT（可选）",
        env_var_path="sources.github.token_env",
        env_var_default="OPENBILICLAW_GITHUB_TOKEN",
        login_url="https://github.com/settings/tokens",
        help_text=(
            "GitHub 公开仓库可匿名发现。可选 PAT 只用于 GET /user 身份确认和提高公开 API "
            "限额；请在设置 / config.toml 中填写，或使用 OPENBILICLAW_GITHUB_TOKEN。"
            "OpenBiliClaw 永远不会读取 GITHUB_TOKEN / GH_TOKEN，也不会访问私有仓库。"
        ),
    ),
    "linuxdo": CredentialSpec(
        slug="linuxdo",
        kinds=("login_state",),
        unverified_reason=(
            "Linux.do Cookie 保留在浏览器中，后端只接收登录布尔值；"
            "公开发现不依赖登录，个人信号同步由插件登录态增强。"
        ),
        form_kind="extension_only",
        form_label="Linux.do 登录态（可选）",
        login_url="https://linux.do/",
        help_text=(
            "公开发现无需登录。要同步本人收藏、点赞和阅读记录，请在浏览器登录 Linux.do；"
            "插件只上报登录状态，不上传 Cookie。"
        ),
    ),
    "v2ex": CredentialSpec(
        slug="v2ex",
        # PATs remain config-owned.  The only value accepted here is the
        # privacy-preserving browser heartbeat: one boolean, never a cookie.
        kinds=("login_state",),
        opaque_credential=True,
        unverified_reason=(
            "V2EX 登录态只保存浏览器上报的布尔值，后端不读取 Cookie；"
            "PAT 在 [sources.v2ex] 或环境变量中配置。"
        ),
        form_kind="none",
        form_label="V2EX PAT（可选）",
        env_var_path="sources.v2ex.token_env",
        env_var_default="OPENBILICLAW_V2EX_TOKEN",
        login_url="https://www.v2ex.com/help/api",
        help_text=(
            "V2EX 公开发现无需登录。可选 PAT 用于识别账号和增强 API 2.0，"
            "请在设置 / config.toml 的 [sources.v2ex] 中填写，或使用 token_env 指定环境变量。"
        ),
    ),
    "weibo": CredentialSpec(
        slug="weibo",
        kinds=("login_state",),
        opaque_credential=True,
        unverified_reason=(
            "微博公开发现可匿名；个人收藏、关注和互动初始化只接受浏览器登录状态布尔值，"
            "后端不读取或保存 Cookie。"
        ),
        form_kind="none",
        form_label="微博浏览器登录态",
        help_text=(
            "公开发现无需登录；要在初始化时导入本人收藏、关注和互动，请登录微博并连接插件。"
            "插件只同步是否登录，实际只读请求在微博页面内执行，不上传 Cookie。"
        ),
    ),
}


@dataclass(frozen=True)
class CredentialVerdict:
    """Outcome of the shared write-time gate.

    ``checked`` is the honesty field: it says how the verdict was reached, and
    ``unverified_reason`` explains a ``none`` rather than letting the caller
    infer that nothing was wrong.
    """

    ok: bool
    error_code: str = ""
    message: str = ""
    checked: CheckDepth = "structural"
    unverified_reason: str = ""
    # Live-probe extras, populated only when ``checked == "live_probe"``.
    authenticated: bool = False
    username: str = ""
    user_id: int = 0
    #: True when ``checked == "live_probe"`` was satisfied from the cache rather
    #: than by a request. The distinction keeps a re-post from re-stamping the
    #: verdict's timestamp: the browser extension re-sends its jar on every
    #: startup, and letting each of those extend the freshness window would keep
    #: a verdict "fresh" indefinitely without anyone ever re-checking it.
    from_cache: bool = False


@dataclass(frozen=True)
class PersistResult:
    """What a successful write actually did.

    ``runtime_dirty`` is separate from ``persisted`` because B站 keeps two
    stores: rewriting the config mirror while the effective cookie is unchanged
    still has to rebuild the runtime, and an unchanged cookie must not, since
    rebuilding cancels and restarts every producer loop for no gain.
    """

    persisted: bool
    cookie_names: tuple[str, ...] = ()
    credential_file: str = ""
    updated_at: str = ""
    upgraded: int = 0
    runtime_dirty: bool = False


@dataclass(frozen=True)
class CredentialWriteOutcome:
    """Everything one write established, before any endpoint projects it.

    Deliberately the *union* of what the six legacy responses report rather
    than their intersection: each of them keeps serving its own shape (the
    browser extension parses all six), so the shared flow has to carry enough
    to reconstruct any of them. Narrowing this to a tidy common subset would
    silently drop ``credential_file`` or ``user_id`` from a response some
    installed extension still reads.
    """

    slug: str
    accepted: bool
    error_code: str = ""
    message: str = ""
    persisted: bool = False
    checked: CheckDepth = "none"
    unverified_reason: str = ""
    cookie_names: tuple[str, ...] = ()
    authenticated: bool = False
    username: str = ""
    user_id: int = 0
    credential_file: str = ""
    updated_at: str = ""
    upgraded: int = 0
    runtime_refreshed: bool = False
    contract: Any = None


def is_masked_echo(value: str) -> bool:
    """Whether *value* is a masked GET echo rather than a real credential.

    A long asterisk run never appears in a genuine Cookie header, unlike a
    single ``*`` which cookie values may legally contain.
    """
    return "****" in value


def cookie_names(value: str) -> tuple[str, ...]:
    """Sorted cookie names in *value*."""
    from openbiliclaw.sources.douyin_direct import parse_cookie_header

    return tuple(sorted(parse_cookie_header(value)))


def credential_fingerprint(slug: str, value: str) -> str:
    """Stable digest of the *login-bearing* part of a credential.

    Answers "is this the same credential the cached verdict was about". Two
    properties have to hold at once, and they pull in opposite directions:

    * A different session must produce a different fingerprint, or a cached
      "logged in" launders a dead cookie onto disk.
    * The *same* session must keep its fingerprint across a re-post, or the
      cache stops working — 抖音's extension re-sends the whole jar every time
      ``msToken`` rotates, which is often, and probing on each rotation is
      exactly the self-inflicted traffic the cache was added to avoid.

    Hashing the whole header satisfies only the first. So the digest covers
    just the names in this platform's :class:`CredentialSpec` gate — the names
    that decide whether a jar can authenticate at all. They are read from the
    spec rather than relisted here, so "what identifies a credential" and "what
    the gate requires" cannot drift apart.

    Returns "" when *value* carries none of them: there is no identity to
    compare, and callers must treat that as "no match" rather than as a match
    between two empty strings.
    """
    spec = CREDENTIAL_SPECS.get(slug)
    if spec is None:
        return ""

    if spec.opaque_credential:
        # A bearer token has no ``name=value`` structure to select from, so the
        # whole secret is its own identity. Domain-separated by slug, as below.
        text = str(value or "").strip()
        if not text:
            return ""
        material = "\x00".join([slug, text])
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    from openbiliclaw.sources.douyin_direct import parse_cookie_header

    pairs = parse_cookie_header(value)
    identity = sorted(set(spec.required_keys) | set(spec.any_of_keys))
    present = [(name, pairs[name]) for name in identity if pairs.get(name)]
    if not present:
        return ""

    # Domain-separated by slug so one platform's jar can never collide with
    # another's, and NUL-joined so no cookie value can forge a pair boundary.
    material = "\x00".join([slug, *(f"{name}={value}" for name, value in present)])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def current_credential(slug: str, *, cfg: Config) -> str:
    """Currently stored credential for *slug*, or ``""``.

    Used to skip re-writing (and re-probing) a byte-identical value: the
    browser extension re-posts the same jar around every startup, and probing
    an unchanged cookie on each of those is a self-inflicted risk-control
    trigger on exactly the platforms that notice.
    """
    try:
        if slug == "bilibili":
            from openbiliclaw.bilibili.auth import resolve_runtime_cookie

            return resolve_runtime_cookie(
                data_dir=cfg.data_path,
                configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
            ).strip()
        if slug == "douyin":
            from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

            return resolve_douyin_cookie(
                data_dir=cfg.data_path,
                cookie_env=str(
                    getattr(cfg.sources.douyin, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE")
                ),
            ).strip()
        if slug == "tiktok":
            from openbiliclaw.sources.tiktok_auth import resolve_tiktok_cookie

            return resolve_tiktok_cookie(
                data_dir=cfg.data_path,
                cookie_env=str(
                    getattr(cfg.sources.tiktok, "cookie_env", "OPENBILICLAW_TIKTOK_COOKIE")
                ),
            ).strip()
        if slug == "twitter":
            from openbiliclaw.sources.x_auth import resolve_x_cookie

            return resolve_x_cookie(
                data_dir=cfg.data_path,
                cookie_env=str(getattr(cfg.sources.twitter, "cookie_env", "OPENBILICLAW_X_COOKIE")),
            ).strip()
    except Exception:  # noqa: BLE001 - an unreadable store means "nothing comparable"
        logger.debug("could not read the current %s credential", slug, exc_info=True)
    return ""


def structural_verdict(slug: str, value: str) -> CredentialVerdict | None:
    """Structural gate for a cookie value; ``None`` when it passes.

    Split out from :func:`validate_credential` because ``PUT /api/config`` needs
    to reach the identical answer, and a second implementation of "which names
    must be present" is precisely the drift that produced D4.
    """
    spec = CREDENTIAL_SPECS.get(slug)
    if spec is None:
        return CredentialVerdict(
            ok=False,
            error_code="unknown_source",
            message=f"未知来源：{slug}",
            checked="none",
        )

    from openbiliclaw.sources.douyin_direct import parse_cookie_header

    # Present *and* non-empty. ``SESSDATA=`` cannot log anyone in, so counting
    # the bare name as satisfied would pass a jar the platform will reject —
    # and it made this gate disagree with rdt-cli's own parser, which drops
    # empty values, so a Reddit cookie could clear validation and then be
    # refused by the store it was validated for.
    pairs = parse_cookie_header(value)
    names = {name for name, raw in pairs.items() if str(raw or "").strip()}
    missing = [name for name in spec.required_keys if name not in names]
    lacks_any = bool(spec.any_of_keys) and not (names & set(spec.any_of_keys))
    if not missing and not lacks_any:
        return None

    return CredentialVerdict(
        ok=False,
        error_code=spec.invalid_error_code,
        message=spec.invalid_message or f"{slug} 凭据结构不完整，未保存。",
        checked="structural",
    )


async def validate_credential(
    slug: str,
    kind: str,
    value: Any,
    *,
    cfg: Config,
    probes: LiveProbeCache = LIVE_PROBES,
) -> CredentialVerdict:
    """The one write-time gate. Every write surface calls exactly this.

    **There is no parameter for checking less.** A ``live=False`` used to exist
    for callers that had "opted out", which in practice meant any request that
    asked: the deprecated B站 route forwarded a request field straight into it,
    so a client could post a structurally complete dead cookie with
    ``validate_with_bilibili=false`` and have it persisted without the probe
    ever running. Both that flag and the unified endpoint's ``validate_live``
    are gone; a platform that can be checked live always is.
    """
    spec = CREDENTIAL_SPECS.get(slug)
    if spec is None:
        return CredentialVerdict(
            ok=False,
            error_code="unknown_source",
            message=f"未知来源：{slug}",
            checked="none",
        )

    if not spec.kinds:
        return CredentialVerdict(
            ok=False,
            error_code="credential_not_writable",
            message=spec.unverified_reason or f"{slug} 不接受凭据写入。",
            checked="none",
            unverified_reason=spec.unverified_reason,
        )

    if kind not in spec.kinds:
        return CredentialVerdict(
            ok=False,
            error_code="unsupported_kind",
            message=(f"{slug} 不支持 kind={kind!r}（支持：{', '.join(spec.kinds)}）。"),
            checked="none",
        )

    if kind == "login_state":
        # A bare boolean. There is nothing to check beyond its type, which the
        # request model already enforced — so say that, rather than returning a
        # success that reads like a confirmation (invariant I5).
        if not isinstance(value, bool):
            return CredentialVerdict(
                ok=False,
                error_code="invalid_login_state",
                message="登录态必须是布尔值。",
                checked="structural",
            )
        return CredentialVerdict(
            ok=True,
            message="已记录浏览器上报的登录态。",
            checked="none",
            unverified_reason=spec.unverified_reason,
        )

    if kind == "token":
        # Content access tokens, not a login credential. Nothing about them can
        # be verified locally, and they say nothing about whether the account is
        # logged in — stated here so the response cannot be misread as one.
        return CredentialVerdict(
            ok=True,
            message="已接收小红书内容访问令牌（xsec_token），它不代表账号登录态。",
            checked="none",
            unverified_reason=spec.unverified_reason,
        )

    text = str(value or "").strip()
    if not text:
        return CredentialVerdict(
            ok=False,
            error_code="empty_cookie",
            message="cookie payload is empty",
            checked="structural",
        )
    if is_masked_echo(text):
        return CredentialVerdict(
            ok=False,
            error_code="masked_echo",
            message="收到的是打码回显值而不是真实 Cookie，未保存。",
            checked="structural",
        )

    structural = structural_verdict(slug, text)
    if structural is not None:
        return structural

    if not spec.live_gate:
        return CredentialVerdict(
            ok=True,
            message="结构校验通过。",
            checked="structural",
            unverified_reason=spec.unverified_reason,
        )

    # A fresh *positive* verdict is reused rather than re-probed. The browser
    # extension re-posts 抖音's jar every time ``msToken`` rotates, which is
    # often, and firing a probe at the platform on each of those is the kind of
    # self-inflicted traffic 抖音 notices. A negative verdict is never reused:
    # someone writing a credential right after a failure is almost certainly
    # fixing it, and replaying the old rejection would refuse the repair.
    #
    # ``peek_matching``, not ``peek``: the reuse is only sound for the credential
    # the verdict is actually about. Keyed on the slug alone, a success recorded
    # for the *old* cookie answered for a different one submitted inside the
    # window — so a structurally complete but dead jar was accepted, stored, and
    # (on the POST path) had its success timestamp refreshed, all without a
    # single request leaving the machine. ``credential_fingerprint`` covers only
    # the login-bearing names, so the ``msToken`` case above still hits.
    cached = probes.peek_matching(slug, credential_fingerprint(slug, text))
    if (
        cached is not None
        and cached.authenticated
        and not cached.network_error
        and cached.is_fresh(PROBE_OK_TTL_SECONDS)
    ):
        return CredentialVerdict(
            ok=True,
            message=cached.detail or "登录态在有效期内已确认。",
            checked="live_probe",
            authenticated=True,
            # Carried through so a cache hit answers as completely as the probe
            # that filled it. Dropping them degraded the deprecated B站 route's
            # response to ``username="", user_id=0`` — still a 200, still
            # ``authenticated``, but no longer field-for-field what the installed
            # extensions parse.
            username=cached.username,
            user_id=cached.user_id,
            from_cache=True,
        )

    from openbiliclaw.api.source_auth.verify import run_live_probe

    # ``record=False``: the verdict is about a *candidate* that is not stored
    # yet. Recording it here would let a rejected paste overwrite the standing
    # verdict about the credential that is actually in use, so the caller
    # records only after the value lands.
    outcome = await run_live_probe(slug, cfg=cfg, cookie=text, probes=probes, record=False)
    if outcome.network_error:
        return CredentialVerdict(
            ok=False,
            error_code="validation_network",
            message=outcome.message,
            checked="live_probe",
        )
    if not outcome.authenticated:
        return CredentialVerdict(
            ok=False,
            error_code="cookie_invalid",
            message=outcome.message,
            checked="live_probe",
        )
    return CredentialVerdict(
        ok=True,
        message=outcome.message,
        checked="live_probe",
        authenticated=True,
        username=outcome.username,
        user_id=outcome.user_id,
    )


def persist_credential(
    slug: str,
    kind: str,
    value: Any,
    *,
    cfg: Config,
    database: Any = None,
    source: str = "settings",
    token_sink: Callable[[list[str]], int] | None = None,
) -> PersistResult:
    """Write a *validated* credential to its platform's store.

    Never validates: calling this with an unchecked value is the bug this
    module was written to prevent, so the gate lives in one place upstream and
    this function stays a dumb writer.
    """
    if kind == "login_state":
        prefix = {
            "xiaohongshu": "xhs",
            "zhihu": "zhihu",
            "linuxdo": "linuxdo",
            "v2ex": "v2ex",
            "weibo": "weibo",
        }.get(slug, slug)
        setter = f"set_{prefix}_login_state"
        getter = f"get_{prefix}_login_state"
        if database is None or not hasattr(database, setter):
            return PersistResult(persisted=False)
        getattr(database, setter)(bool(value))
        updated_at = ""
        try:
            _stored, updated_at = getattr(database, getter)()
        except Exception:  # noqa: BLE001 - a read-back failure is not a write failure
            logger.debug("could not read back the %s login state", slug, exc_info=True)
        return PersistResult(persisted=True, updated_at=str(updated_at or ""))

    if kind == "token":
        urls = [str(url) for url in (value or []) if str(url or "").strip()]
        upgraded = token_sink(urls) if token_sink is not None else 0
        return PersistResult(persisted=bool(urls), upgraded=upgraded)

    text = str(value or "").strip()
    names = cookie_names(text)

    if slug == "bilibili":
        from contextlib import suppress

        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import load_config_with_diagnostics, save_config

        manager = AuthManager(data_dir=cfg.data_path)
        stored = ""
        with suppress(Exception):
            stored = manager.load_cookie().strip()

        # config.toml stays a mirror so ``config-show`` and the settings page
        # agree with the runtime store. Reloaded rather than reusing *cfg* so a
        # caller holding a partially-mutated config cannot flush its edits here
        # as a side effect of saving a cookie.
        fresh, diagnostics = load_config_with_diagnostics()
        configured = fresh.bilibili.cookie.strip()

        if stored != text:
            manager.set_cookie(text)
        if configured != text:
            fresh.bilibili.cookie = text
            save_config(fresh, diagnostics.config_path)

        return PersistResult(
            persisted=stored != text or configured != text,
            cookie_names=names,
            runtime_dirty=(configured or stored) != text or configured != text,
        )

    if slug == "douyin":
        from openbiliclaw.sources.douyin_auth import DouyinCookieManager

        DouyinCookieManager(cfg.data_path).set_cookie(text, source=source)
        return PersistResult(persisted=True, cookie_names=names)

    if slug == "tiktok":
        from openbiliclaw.sources.tiktok_auth import TiktokCookieManager

        TiktokCookieManager(cfg.data_path).set_cookie(text, source=source)
        return PersistResult(persisted=True, cookie_names=names)

    if slug == "twitter":
        from openbiliclaw.sources.x_auth import XCookieManager

        XCookieManager(cfg.data_path).set_cookie(text, source=source)
        return PersistResult(persisted=True, cookie_names=names)

    if slug == "reddit":
        from openbiliclaw.sources.reddit_tasks import sync_rdt_credential_from_cookie_header

        result = sync_rdt_credential_from_cookie_header(text, source=source)
        return PersistResult(
            persisted=bool(result.has_cookie),
            cookie_names=tuple(result.cookie_names),
            credential_file=str(result.credential_file),
        )

    return PersistResult(persisted=False)
