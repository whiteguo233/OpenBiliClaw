"""Real signed-sandbox smoke: HTTP, optional model inference, host quit/crash cleanup.

No login items or system VPN settings are changed. Test profiles are confined
by the native host to its own Testing/ directory. Optional models are imported
via Ollama's public blob API, without reading another app's container.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def wait_for(check, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (httpx.HTTPError, OSError):
            pass
        time.sleep(0.5)
    raise TimeoutError("Sandbox smoke condition did not become ready")


def snapshot_children(parent):
    output = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
    children = [tuple(map(int, line.split())) for line in output.splitlines()]
    owned = {parent}
    while True:
        discovered = {pid for pid, ppid in children if ppid in owned}
        if discovered.issubset(owned):
            return owned - {parent}
        owned.update(discovered)


def import_model(client, endpoint, root, model, tag):
    manifest = json.loads((root / "manifests/registry.ollama.ai/library" / model / tag).read_text())
    layer = next(item for item in manifest["layers"] if item["mediaType"].endswith(".model"))
    digest = layer["digest"]
    with (root / "blobs" / digest.replace(":", "-")).open("rb") as stream:
        response = client.post(
            endpoint + "/api/blobs/" + digest, content=iter(lambda: stream.read(1024 * 1024), b"")
        )
        response.raise_for_status()
    response = client.post(
        endpoint + "/api/create",
        json={
            "model": model + ":" + tag,
            "files": {model + ".gguf": digest},
            "stream": False,
        },
    )
    response.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18550)
    parser.add_argument("--models-root", type=Path)
    parser.add_argument("--crash-host", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    licenses = args.app.resolve() / "Contents/Resources/ThirdPartyLicenses"
    records = json.loads((licenses / "manifest.json").read_text())["packages"]
    assert {item["name"] for item in records} == {"bilibili-api-python", "browser-cookie3"}
    for item in records:
        assert item["version"]
        assert list((licenses / item["name"]).glob("LICENSE-*.txt"))
    profile = "smoke-" + str(time.time_ns())
    executable = args.app.resolve() / "Contents/MacOS/OpenBiliClaw"
    proc = subprocess.Popen(
        [
            str(executable),
            "--headless",
            "--qa-profile",
            profile,
            "--qa-port",
            str(args.port),
            "--quit-after",
            "240",
            "--qa-native-selftest",
            "--qa-ui-state",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    transcript = args.output / "runtime.log"

    def collect():
        with transcript.open("w") as log:
            for line in proc.stdout:
                if line.startswith("OBC_QA_UI_STATE="):
                    pending = args.output / "ui-state.json.tmp"
                    pending.write_text(line.split("=", 1)[1])
                    pending.replace(args.output / "ui-state.json")
                else:
                    log.write(line)
                    log.flush()

    reader = threading.Thread(target=collect, daemon=True)
    reader.start()
    report = {
        "profile": profile,
        "host_pid": proc.pid,
        "identified_license_records": len(records),
        "requests": {},
    }
    owned = set()
    ownership_lock = threading.Lock()
    monitor_done = threading.Event()

    def remember_children():
        snapshot = snapshot_children(proc.pid)
        with ownership_lock:
            owned.update(snapshot)

    def monitor():
        while not monitor_done.is_set():
            remember_children()
            monitor_done.wait(0.5)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        with httpx.Client(
            trust_env=False, timeout=120, headers={"Origin": f"http://127.0.0.1:{args.port}"}
        ) as client:
            base = f"http://127.0.0.1:{args.port}"
            wait_for(lambda: client.get(base + "/api/ping", timeout=2).status_code == 200)
            print("signed sandbox backend is listening", flush=True)
            for path in (
                "/api/ping",
                "/api/runtime-status",
                "/api/qr-info",
                "/web/",
                "/m/",
                "/shared/mobile-connection.js",
                "/i18n/ui/en.json",
                "/api/update-status",
                "/api/autostart-status",
            ):
                response = client.get(base + path)
                response.raise_for_status()
                report["requests"][path] = response.status_code
            qr = client.get(base + "/api/qr-info").json()
            assert qr["api_host"] == "0.0.0.0"
            if qr.get("lan_ip"):
                response = client.get(f"http://{qr['lan_ip']}:{args.port}/m/")
                response.raise_for_status()
                report["lan_address_http"] = response.status_code
            update = client.get(base + "/api/update-status").json()["backend"]
            assert update["install_mode"] == "mac_app_store"
            assert update["reason"] == "app_store_managed"
            assert update["auto_update_enabled"] is False
            rejected = client.post(
                base + "/api/update/apply", json={"target": "backend", "tag": "backend-v9.0.0"}
            )
            assert rejected.status_code == 409
            assert rejected.json()["reason"] == "app_store_managed"
            report["update_apply"] = 409
            cfg = client.get(base + "/api/config").json()
            embedding = cfg["llm"]["embedding"]
            endpoint = embedding["base_url"].removesuffix("/v1").rstrip("/")
            assert urlsplit(endpoint).hostname == "127.0.0.1"
            assert urlsplit(endpoint).port != 11434
            response = client.get(endpoint + "/api/version")
            response.raise_for_status()
            report["bundled_ollama_version"] = response.json()["version"]
            remember_children()
            assert len(snapshot_children(proc.pid)) >= 6, (
                owned
            )  # API + four workers + private Ollama
            print("Web, mobile, LAN, store boundaries and private Ollama HTTP passed", flush=True)
            if args.models_root:
                for model, tag in (("bge-m3", "latest"), ("qwen2.5", "0.5b")):
                    import_model(client, endpoint, args.models_root, model, tag)
                print("public model blobs imported into isolated sandbox via HTTP", flush=True)
                vectors = client.post(
                    endpoint + "/api/embed",
                    json={"model": "bge-m3", "input": ["沙盒中的真实向量请求"]},
                )
                vectors.raise_for_status()
                vector = vectors.json()["embeddings"][0]
                assert len(vector) == 1024 and any(vector)
                report["embedding_dimensions"] = len(vector)
                reply = client.post(
                    endpoint + "/api/generate",
                    json={
                        "model": "qwen2.5:0.5b",
                        "prompt": "Reply with OK.",
                        "stream": False,
                        "options": {"num_predict": 12, "num_ctx": 2048, "temperature": 0},
                    },
                )
                reply.raise_for_status()
                assert reply.json().get("response")
                report["model_generated_text"] = True
                configured = client.put(
                    base + "/api/config",
                    json={
                        "llm": {
                            "instances": {
                                "store_qa": {
                                    "provider_type": "ollama",
                                    "model": "qwen2.5:0.5b",
                                    "base_url": endpoint + "/v1",
                                    "enabled": True,
                                }
                            },
                            "default_chain": ["store_qa"],
                            "default_provider": "store_qa",
                        }
                    },
                )
                configured.raise_for_status()
                wait_for(
                    lambda: not client.get(base + "/api/ping", timeout=3).json().get("degraded"),
                    timeout=60,
                )
                report["backend_recovered_from_unconfigured"] = True
                print(
                    "real embedding, generation and backend configuration recovery passed",
                    flush=True,
                )
            remember_children()

        # Warm machines can finish the HTTP probes before AppKit's six-second
        # layout timer fires. Preserve that evidence before killing the host.
        def native_ready():
            path = args.output / "ui-state.json"
            return path.is_file() and json.loads(path.read_text())["controls"][0]["enabled"]

        wait_for(native_ready, timeout=30)
        if args.crash_host:
            proc.kill()
        print("waiting for native host quit and owned service cleanup", flush=True)
        proc.wait(timeout=260)
        monitor_done.set()
        monitor_thread.join(timeout=3)

        def all_gone():
            for pid in owned:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                return False
            return True

        wait_for(all_gone, timeout=60)
        report["owned_processes_reaped"] = len(owned)
        reader.join(timeout=3)
        assert "OBC_QA_TAILNET_EXIT=0" in transcript.read_text()
        report["tailnet_binary_selftest"] = True
        controls = json.loads((args.output / "ui-state.json").read_text())["controls"]
        assert len(controls) == 3 and all(
            item["inside_window"] and item["has_action"] for item in controls
        )
        report["native_control_layout"] = True
        (args.output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)


if __name__ == "__main__":
    main()
