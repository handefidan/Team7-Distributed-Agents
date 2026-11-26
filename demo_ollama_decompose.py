"""Team 7 — Live Local Ollama Task Decomposition Demo.

Connects to the local Ollama server (http://localhost:11434), picks an installed
model (defaults to llama3.2:3b or the first available model), decomposes a given
software requirement into subtasks with dependencies, and maps them via
DependencyManager.

Usage:
    .venv/bin/python3 demo_ollama_decompose.py
    .venv/bin/python3 demo_ollama_decompose.py "Build a real-time chat app with rooms and auth"
    .venv/bin/python3 demo_ollama_decompose.py --model qwen3:8b "Build an e-commerce checkout API"
"""

import argparse
import asyncio
import os
import sys
import time

import httpx

from master.decompose import decompose_requirement
from master.dependency import DependencyManager

OLLAMA_BASE_URL = os.getenv("MASTER_OLLAMA_BASE_URL", "http://localhost:11434")
DEFAULT_REQUIREMENT = (
    "Build a Library Management System with FastAPI REST APIs, a relational database "
    "schema for books and members, borrowing/return tracking with overdue fine calculation, "
    "a simple web UI, automated pytest tests, and API documentation."
)


async def detect_model(client: httpx.AsyncClient, preferred: str | None) -> str:
    resp = await client.get("/api/tags", timeout=5.0)
    resp.raise_for_status()
    installed = [m["name"] for m in resp.json().get("models", []) if m.get("name")]
    if not installed:
        raise RuntimeError("Ollama çalışıyor ama hiç model kurulu değil (`ollama pull llama3.2:3b`).")
    if preferred:
        if preferred in installed:
            return preferred
        print(f"⚠️  '{preferred}' kurulu değil (kurulu modeller: {', '.join(installed)}). '{installed[0]}' kullanılıyor.")
    for fast in ("llama3.2:3b", "llama3.2", "qwen3:4b", "qwen3:8b"):
        if fast in installed:
            return fast
    return installed[0]


async def run_demo(requirement: str, model_arg: str | None) -> None:
    print("=" * 72)
    print("⚡ Team 7 — Canlı Yerel Ollama Görev Ayrıştırma (Decompose) Demosu")
    print("=" * 72)

    async with httpx.AsyncClient(base_url=OLLAMA_BASE_URL, timeout=180.0) as client:
        try:
            model = await detect_model(client, model_arg)
        except Exception as exc:
            print(f"❌ Yerel Ollama'ya ({OLLAMA_BASE_URL}) bağlanılamadı: {exc}")
            sys.exit(1)

        print(f"🟢 Ollama Bağlandı : {OLLAMA_BASE_URL}")
        print(f"🤖 Seçilen Model   : {model}")
        print(f"📝 Gereksinim      : {requirement}\n")
        print("⏳ Model görevi alt görevlere ayırıyor (decompose)...")

        started = time.perf_counter()
        decomp = await decompose_requirement(requirement, client, model)
        elapsed = time.perf_counter() - started

    is_mock = decomp.summary.startswith("Mock decomposition")
    source_tag = "⚠️  MOCK FALLBACK (Ollama yanıtı şemaya uymadı)" if is_mock else f"🟢 CANLI OLLAMA ({model})"

    dep_manager = DependencyManager()
    mapped = dep_manager.register_decomposition(decomp)
    id_to_label = {t.task_uuid: t.label for t in mapped}
    ready = dep_manager.get_ready_tasks()

    print("\n" + "-" * 72)
    print(f"Kaynak   : {source_tag}")
    print(f"Süre     : {elapsed:.2f} sn")
    print(f"Proje    : {decomp.project_title}")
    print(f"Özet     : {decomp.summary}")
    print(f"Görevler : {len(mapped)} alt görev ({len(ready)} tanesi hemen dağıtıma hazır)")
    print("-" * 72)

    for t in mapped:
        deps = ", ".join(id_to_label[d] for d in t.dependency_uuids) or "Yok (bağımsız)"
        caps = t.required_capabilities
        print(f"\n[{t.label}] {t.title}")
        print(f"   Kategori   : {t.category.value} | Durum: {t.status.value.upper()}")
        print(f"   Önkoşullar : {deps}")
        print(f"   Yetenekler : coding>={caps.coding}, reasoning>={caps.reasoning}, context>={caps.context}")
        print(f"   Açıklama   : {t.description}")

    print("\n" + "=" * 72)
    print("🚀 İlk Başlatılacak (Hazır) Görevler:", ", ".join(f"{t.label} ({t.title})" for t in ready))
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Live local Ollama task decomposition demo.")
    parser.add_argument("requirement", nargs="*", help="Software requirement text to decompose.")
    parser.add_argument("--model", default=None, help="Ollama model name (default: auto-detect).")
    args = parser.parse_args()
    req_text = " ".join(args.requirement).strip() or DEFAULT_REQUIREMENT
    asyncio.run(run_demo(req_text, args.model))


if __name__ == "__main__":
    main()
