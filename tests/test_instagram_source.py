"""Strict Instagram media/user normalization regressions."""

from __future__ import annotations

from datetime import UTC, datetime

from openbiliclaw.sources.instagram import (
    instagram_media_to_content,
    instagram_user_to_content,
)


def test_instagram_media_normalizes_stable_identity_url_author_and_time() -> None:
    content = instagram_media_to_content(
        {
            "id": "3712345678901234567",
            "code": "DAb_cd-123",
            "content_type": "reel",
            "url": "https://evil.example/reel/replaced/",
            "description": "  A small robotics demo\nwith captions  ",
            "cover_url": "https://scontent-lax3-2.cdninstagram.com/image.jpg",
            "author_id": "25025320",
            "author_name": "openai",
            "published_at": "2026-08-10T12:30:00Z",
            "like_count": 999,
            "play_count": 123456,
        },
        strategy="instagram-topic",
        source_keyword_id=17,
        now=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert content is not None
    assert content.content_id == "3712345678901234567"
    assert content.item_key == "instagram:3712345678901234567"
    assert content.content_url == "https://www.instagram.com/reel/DAb_cd-123/"
    assert content.content_type == "reel"
    assert content.author_name == "openai"
    assert content.published_at == "2026-08-10T12:30:00Z"
    assert content.source_keyword_id == 17


def test_instagram_media_normalizes_polaris_typename_and_prefers_pk() -> None:
    content = instagram_media_to_content(
        {
            # Newer Comet/Relay SSR payloads use a ``POLARIS_``-prefixed id
            # while the numeric media identity lives in ``pk``.
            "id": "POLARIS_3857778825493088696",
            "pk": "3857778825493088696",
            "code": "DWJlWcDigm4",
            "__typename": "XIGPolarisVideoMedia",
            "caption": {"text": "Public caption"},
            "display_uri": "https://scontent-lax3-2.cdninstagram.com/image.jpg",
            "user": {"id": "17841403706175141", "username": "setupspawn"},
        }
    )

    assert content is not None
    assert content.content_id == "3857778825493088696"
    assert content.content_type == "reel"
    assert content.content_url == "https://www.instagram.com/reel/DWJlWcDigm4/"
    assert content.author_name == "setupspawn"


def test_instagram_normalization_does_not_fabricate_engagement() -> None:
    content = instagram_media_to_content(
        {
            "id": "3712345678901234567",
            "code": "DAb_cd-123",
            "content_type": "post",
            "description": "Public caption",
            # Even if a drifted/untrusted row includes counters, the frozen
            # all-fetch-path contract declares them unavailable.
            "view_count": 10,
            "play_count": 20,
            "like_count": 30,
            "favorite_count": 40,
            "comment_count": 50,
            "share_count": 60,
            "danmaku_count": 70,
        }
    )

    assert content is not None
    assert content.engagement_available == []
    assert (
        content.view_count,
        content.like_count,
        content.favorite_count,
        content.comment_count,
        content.share_count,
        content.danmaku_count,
    ) == (0, 0, 0, 0, 0, 0)


def test_instagram_normalizer_rejects_unstable_or_cross_host_identity() -> None:
    assert (
        instagram_media_to_content(
            {"id": "not-numeric", "code": "DAb_cd-123", "content_type": "post"}
        )
        is None
    )
    assert (
        instagram_media_to_content(
            {
                "id": "3712345678901234567",
                "content_type": "post",
                "url": "https://evil.example/p/DAb_cd-123/",
            }
        )
        is None
    )
    assert (
        instagram_media_to_content(
            {
                "id": "3712345678901234567",
                "code": "DAb_cd-123",
                "content_type": "unknown",
            }
        )
        is None
    )


def test_instagram_normalizer_does_not_stringify_containers_or_use_future_time() -> None:
    content = instagram_media_to_content(
        {
            "id": "3712345678901234567",
            "code": "DAb_cd-123",
            "content_type": "carousel",
            "description": ["not", "text"],
            "title": {"not": "text"},
            "author_name": ["not", "text"],
            "published_at": "2027-01-01T00:00:00Z",
            "cover_url": "https://example.com/image.jpg",
        },
        now=datetime(2026, 8, 12, tzinfo=UTC),
    )

    assert content is not None
    assert content.title == "Instagram Carousel"
    assert content.body_text == ""
    assert content.author_name == ""
    assert content.published_at == ""
    assert content.cover_url == ""
    assert "['not'" not in repr(content)


def test_instagram_user_normalizer_keeps_user_identity_separate_from_media() -> None:
    content = instagram_user_to_content(
        {
            "id": "25025320",
            "content_type": "user",
            "url": "https://www.instagram.com/OpenAI/?hl=en",
            "author_name": "OpenAI",
            "description": "Research and deployment company",
            "cover_url": "https://instagram.fabc1-1.fna.fbcdn.net/profile.jpg",
        },
        source_keyword_id=4,
    )

    assert content is not None
    assert content.content_id == "user:25025320"
    assert content.item_key == "instagram:user:25025320"
    assert content.content_url == "https://www.instagram.com/openai/"
    assert content.content_type == "user"
    assert content.author_name == "openai"
    assert content.source_keyword_id == 4
