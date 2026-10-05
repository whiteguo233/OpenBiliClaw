from __future__ import annotations

import asyncio

from openbiliclaw.soul.dialogue_learn_queue import (
    DialogueJob,
    DialogueJobKind,
    DialogueJobResult,
    DialogueSettlementQueue,
)


async def test_non_completion_jobs_are_dropped_when_queue_is_full() -> None:
    blocker_entered = asyncio.Event()
    release = asyncio.Event()

    async def dispatcher(job: DialogueJob) -> DialogueJobResult:
        if job.payload.get("blocker"):
            blocker_entered.set()
            await release.wait()
        return DialogueJobResult(outcome="completed")

    queue = DialogueSettlementQueue(dispatcher, max_depth=5)
    queue.start()
    queue.submit(DialogueJobKind.LEARN, {"blocker": True})
    await asyncio.wait_for(blocker_entered.wait(), timeout=1)

    submitted = 0
    dropped = 0
    for index in range(20):
        job = queue.submit(
            DialogueJobKind.SETTLE_HYPOTHESIS,
            {"target_kind": "hypothesis", "target_ref": f"h-{index}"},
        )
        if job is None:
            dropped += 1
        else:
            submitted += 1

    assert queue.depth <= queue.max_depth
    assert dropped > 0
    assert queue.dropped_jobs == dropped
    assert submitted + dropped == 20

    release.set()
    await queue.shutdown(timeout=2)
