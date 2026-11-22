"""Team 7 — Local Demo Simulation Runner.

Starts Master (:8000), Coder Agent (:8001), and Reviewer Agent (:8002) locally
and registers the workers with the Master.

Usage:  python run_local_demo.py
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

MASTER_PORT = 8000
WORKERS = [
    ("coder-agent", 8001, "llama3.2:3b", {"coding": 92, "reasoning": 82, "context": 70}),
    ("reviewer-agent", 8002, "qwen2.5-coder:1.5b", {"coding": 70, "reasoning": 92, "context": 90}),
]


def wait_for_port(port: int, timeout: float = 10.0) -> bool:
    url = f"http://127.0.0.1:{port}/" if port == MASTER_PORT else f"http://127.0.0.1:{port}/agent/v1/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status in (200, 404):
                    return True
        except Exception:
            time.sleep(0.3)
    return False


def register_agent(agent_id: str, port: int, model: str, capabilities: dict) -> bool:
    payload = json.dumps({
        "agent_id": agent_id,
        "base_url": f"http://127.0.0.1:{port}",
        "model": model,
        "capabilities": capabilities,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{MASTER_PORT}/api/v1/agents",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 201
    except Exception as e:
        print(f"[{agent_id}] Kayıt hatası: {e}")
        return False


def main() -> None:
    print("=" * 65)
    print("⚡ Team 7 — Dağıtık Çoklu-Ajan Görev Dağıtım Demosu Başlatılıyor...")
    print("=" * 65)

    base_env = {**os.environ, "TAILSCALE_IP": "127.0.0.1", "AGENT_MOCK": "true"}
    processes: list[subprocess.Popen] = []

    try:
        print(f"[1/{len(WORKERS) + 1}] Master Agent başlatılıyor (Port {MASTER_PORT})...")
        processes.append(
            subprocess.Popen([sys.executable, "-m", "master.main"], env={**base_env, "MASTER_PORT": str(MASTER_PORT)})
        )

        for idx, (agent_id, port, model, _) in enumerate(WORKERS, start=2):
            print(f"[{idx}/{len(WORKERS) + 1}] Worker ({agent_id}) başlatılıyor (Port {port})...")
            worker_env = {**base_env, "AGENT_ID": agent_id, "AGENT_PORT": str(port), "AGENT_MODEL": model}
            processes.append(subprocess.Popen([sys.executable, "-m", "agent.main"], env=worker_env))

        for port in [MASTER_PORT, *(w[1] for w in WORKERS)]:
            if not wait_for_port(port):
                print(f"HATA: Port {port} zamanında yanıt vermedi!")

        if all(register_agent(*w) for w in WORKERS):
            print("✅ Worker ajanlar başarıyla Master'a bağlandı!")

        print(f"\n🎉 DEMO HAZIR! Tarayıcıda açın: http://localhost:{MASTER_PORT}")
        print("Sonlandırmak için Ctrl + C tuşlarına basın...\n")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nKapatılıyor...")
    finally:
        for p in processes:
            p.terminate()
            p.wait()
        print("Tüm servisler durduruldu.")


if __name__ == "__main__":
    main()
