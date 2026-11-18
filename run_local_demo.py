"""Team 7 — Local Demo Simulation Runner.

Tek bir komutla yerel bilgisayarınızda:
1. Master Agent'ı (Port 8000),
2. Coder Worker Agent'ı (Port 8001),
3. Reviewer Worker Agent'ı (Port 8002)
başlatır ve otomatik olarak Master'a kaydeder.

Kullanım:
    python run_local_demo.py
"""

import os
import sys
import time
import signal
import subprocess
import urllib.request
import json

def wait_for_port(port, timeout=10):
    url = f"http://127.0.0.1:{port}/" if port == 8000 else f"http://127.0.0.1:{port}/agent/v1/health"
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as resp:
                if resp.status in (200, 404):
                    return True
        except Exception:
            time.sleep(0.3)
    return False

def register_agent(agent_id, base_url, model, capabilities):
    data = json.dumps({
        "agent_id": agent_id,
        "base_url": base_url,
        "model": model,
        "capabilities": capabilities
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/v1/agents",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 201
    except Exception as e:
        print(f"[{agent_id}] Kayıt hatası: {e}")
        return False

def main():
    print("=" * 65)
    print("⚡ Team 7 — Dağıtık Çoklu-Ajan Görev Dağıtım Demosu Başlatılıyor...")
    print("=" * 65)

    env = os.environ.copy()
    env["TAILSCALE_IP"] = "127.0.0.1"
    env["AGENT_MOCK"] = "true"  # Ollama çalışmıyorsa bile simülasyon çıktısı üretir

    python_exe = sys.executable

    processes = []
    try:
        # 1. Master Agent (:8000)
        print("[1/3] Master Agent başlatılıyor (Port 8000)...")
        master_env = env.copy()
        master_env["MASTER_PORT"] = "8000"
        p_master = subprocess.Popen([python_exe, "-m", "master.main"], env=master_env)
        processes.append(p_master)

        # 2. Worker 1: Coder Agent (:8001)
        print("[2/3] Worker 1 (coder-agent) başlatılıyor (Port 8001)...")
        w1_env = env.copy()
        w1_env["AGENT_ID"] = "coder-agent"
        w1_env["AGENT_PORT"] = "8001"
        w1_env["AGENT_MODEL"] = "llama3.2:3b"
        p_w1 = subprocess.Popen([python_exe, "-m", "agent.main"], env=w1_env)
        processes.append(p_w1)

        # 3. Worker 2: Reviewer Agent (:8002)
        print("[3/3] Worker 2 (reviewer-agent) başlatılıyor (Port 8002)...")
        w2_env = env.copy()
        w2_env["AGENT_ID"] = "reviewer-agent"
        w2_env["AGENT_PORT"] = "8002"
        w2_env["AGENT_MODEL"] = "qwen2.5-coder:1.5b"
        p_w2 = subprocess.Popen([python_exe, "-m", "agent.main"], env=w2_env)
        processes.append(p_w2)

        print("\nServislerin ayağa kalkması bekleniyor...")
        for port in [8000, 8001, 8002]:
            if not wait_for_port(port):
                print(f"HATA: Port {port} zamanında yanıt vermedi!")

        # Ajanları Master'a kaydet
        print("\nAjanlar Master düğümüne kaydediliyor...")
        r1 = register_agent(
            "coder-agent",
            "http://127.0.0.1:8001",
            "llama3.2:3b",
            {"coding": 92, "reasoning": 82, "context": 70}
        )
        r2 = register_agent(
            "reviewer-agent",
            "http://127.0.0.1:8002",
            "qwen2.5-coder:1.5b",
            {"coding": 70, "reasoning": 92, "context": 90}
        )

        if r1 and r2:
            print("✅ 'coder-agent' ve 'reviewer-agent' başarıyla Master'a bağlandı!")

        print("\n" + "=" * 65)
        print("🎉 DEMO SİSTEMİ AKTİF VE HAZIR!")
        print("👉 Tarayıcınızda şu adresi açın: http://localhost:8000")
        print("=" * 65)
        print("\nNasıl Deneyebilirsiniz?")
        print(" 1. Tarayıcıda 'Hazır Şablon Seç' menüsünden 'Kütüphane Yönetim Sistemi'ni seçin.")
        print(" 2. 'Görevleri Ayrıştır & DAG Grafiği Çıkar' butonuna tıklayın.")
        print(" 3. '🚀 Otomatik Tüm Hattı Çalıştır (Auto-Run)' veya 'Sıradaki Görevi Dağıt' butonuna basın.")
        print(" 4. Görevlerin yeteneklerine göre 'coder-agent' ve 'reviewer-agent' arasında nasıl dağıtıldığını izleyin!")
        print("\nSonlandırmak için Ctrl + C tuşlarına basın...\n")

        # Keep alive
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nKapatılıyor...")
    finally:
        for p in processes:
            p.terminate()
            p.wait()
        print("Tüm servisler başarıyla durduruldu.")

if __name__ == "__main__":
    main()
