"""Source-view compatibility and exact UTF-8 ceilings through its public seam."""

from __future__ import annotations

import hashlib
import json

import pytest

from openbiliclaw.discovery.jev_context import candidate_text_view


def _item() -> dict[str, object]:
    paragraphs = [f"背景片段{i}：描述沿途风景。" for i in range(400)]
    paragraphs[150] = paragraphs[301] = "独特的普通段落用于占位。"
    paragraphs[300] = "如果证据缺失，则停止结论。"
    return {
        "title": '算法原理 "quoted" 🐈',
        "body_text": "\n".join(paragraphs),
        "tags": ["甲", "x\\y"],
        "duration": 12,
    }


@pytest.mark.parametrize(
    ("budget", "size", "digest"),
    [
        (3000, 2935, "1f0eb4f8095bdfb4a0ac9f402a1bcfce97374dd3b4c0e7990a4150750836fa03"),
        (12000, 11959, "4de4428bcdfa1529e27607454b40e3e0c6f781a57237207d522c73eb72b1ffa3"),
        (18000, 17990, "2584188f7f53222fe80df7198681d1de8fe1f27332ef93a93641529407a4b4f5"),
        (24000, 23906, "e2be4e96a796fbc77669ef427a5073bbb3db0dc9ee1795e53751baae65bb9f2f"),
    ],
)
def test_budgeted_source_view_is_byte_identical_to_original_policy(
    budget: int, size: int, digest: str
) -> None:
    # Golden measurements captured from source-spans-v1 before the additive
    # sizing optimization. They include quotes, UTF-8, metadata and dedup origins.
    view = candidate_text_view(_item(), max_bytes=budget)
    assert view is not None
    wire = json.dumps(
        view.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    assert len(wire) == size <= budget
    assert hashlib.sha256(wire).hexdigest() == digest


@pytest.mark.parametrize("count", [0, 1, 9, 10, 99, 100, 254, 255])
def test_source_view_respects_exact_boundary_bytes_and_choice_limit(count: int) -> None:
    item = {"body_text": "\n".join(f"普通原句{i} 🐈" for i in range(count))}
    for budget in (300, 1200, 3000, 12000, 32000):
        view = candidate_text_view(item, max_bytes=budget)
        if view is None:
            continue
        wire = json.dumps(
            view.content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        assert len(wire) <= budget
        assert len(view.excerpts) <= 254
        assert view.content["text_coverage"]["is_partial"] is (len(view.excerpts) < count)
