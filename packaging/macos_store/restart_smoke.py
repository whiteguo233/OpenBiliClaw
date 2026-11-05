"""Verify store model ownership across a configured restart and IPv6 binding.

Uses a fresh profile, an unconnected chat endpoint and no model downloads.
The embedded embedding service must keep its own port and stop on both quits.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18590)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = args.app.resolve() / "Contents/MacOS/OpenBiliClaw Backend"
    profile = "mixed-restart-" + str(time.time_ns())
    report = {"profile": profile}

    def wait(check, timeout=45):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                result = check()
                if result:
                    return result
            except (httpx.HTTPError, OSError):
                pass
            time.sleep(0.3)
        raise TimeoutError("restart check timed out")

    with httpx.Client(trust_env=False, timeout=2) as c:
        for attempt in (1, 2):
            env = os.environ.copy()
            env["OPENBILICLAW_HOST"] = "0.0.0.0" if attempt == 1 else "::1"
            base = f"http://127.0.0.1:{args.port}" if attempt == 1 else f"http://[::1]:{args.port}"
            with open(args.output / f"run-{attempt}.log", "w") as log:
                proc = subprocess.Popen(
                    [
                        str(app),
                        "--headless",
                        "--qa-profile",
                        profile,
                        "--qa-port",
                        str(args.port),
                        "--qa-ui-state",
                        "--quit-after",
                        "25",
                    ],
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    wait(lambda base=base: c.get(base + "/api/ping").status_code == 200)
                    if attempt == 1:
                        cfg = c.get(base + "/api/config").json()
                        endpoint = (
                            cfg["llm"]["embedding"]["base_url"].removesuffix("/v1").rstrip("/")
                        )
                        report["private_embedding_endpoint"] = endpoint
                        with socket.socket() as s:
                            s.bind(("127.0.0.1", 0))
                            external_port = s.getsockname()[1]
                        response = c.put(
                            base + "/api/config",
                            headers={"Origin": base},
                            json={
                                "llm": {
                                    "instances": {
                                        "separate_chat": {
                                            "provider_type": "ollama",
                                            "enabled": True,
                                            "model": "qa-not-downloaded",
                                            "base_url": f"http://127.0.0.1:{external_port}/v1",
                                        }
                                    },
                                    "default_chain": ["separate_chat"],
                                    "default_provider": "separate_chat",
                                }
                            },
                        )
                        response.raise_for_status()
                        report["chat_endpoint_port"] = external_port
                    else:
                        wait(
                            lambda endpoint=endpoint: (
                                c.get(endpoint + "/api/version").status_code == 200
                            )
                        )

                        def native_ready():
                            rows = [
                                line
                                for line in (args.output / "run-2.log").read_text().splitlines()
                                if line.startswith("OBC_QA_UI_STATE=")
                            ]
                            return (
                                bool(rows)
                                and json.loads(rows[-1].split("=", 1)[1])["controls"][0]["enabled"]
                            )

                        wait(native_ready, timeout=20)
                        report["native_ipv6_open_button_ready"] = True
                        report["private_embedding_restarted"] = True
                        report["ipv6_listener_ping"] = c.get(base + "/api/ping").status_code
                    assert proc.wait(timeout=60) == 0, "native app did not exit cleanly"

                    def private_stopped(endpoint=endpoint):
                        try:
                            c.get(endpoint + "/api/version")
                            return False
                        except httpx.ConnectError:
                            return True

                    wait(private_stopped, timeout=10)
                    report["private_daemon_stopped_after_run_" + str(attempt)] = True
                finally:
                    if proc.poll() is None:
                        proc.terminate()
                        proc.wait(timeout=10)
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
