"""Fingerprint the reviewed Core side of the sponsored IPC for release tooling."""

import hashlib
from pathlib import Path


def client_digest() -> str:
    """Hash relative names and content; Windows and Unix checkouts must agree."""
    root = Path(__file__).resolve().parents[1]
    sources = sorted((root / "llm").glob("sponsored_*.py"))
    if (root / "llm/sponsored_protocol.py") not in sources:
        raise ValueError("sponsored IPC sources are missing")
    sources.append(root / "sponsored_runtime_state.py")
    digest = hashlib.sha256()
    for path in sources:
        content = path.read_bytes().replace(b"\r\n", b"\n")
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(content).digest())
    return digest.hexdigest()


if __name__ == "__main__":
    print(client_digest())
