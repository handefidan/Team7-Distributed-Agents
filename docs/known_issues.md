# Known Issues — Team 7 Distributed Multi-Agent System

**Maintainer:** Eyad Ahmed Mahmoud Zaidan
**Last Updated:** Sprint 5 (Week 9)

This document tracks known bugs, limitations, and environment quirks discovered during multi-machine testing and regression runs.

| ID | Component | Issue Description | Impact | Workaround / Status |
| :--- | :--- | :--- | :--- | :--- |
| **KI-01** | Tests (`test_dashboard_endpoints.py`) | `test_plan_tasks_and_reset` asserts `len(res) == 5` (matching the offline mock fallback), but fails when a live local Ollama server is running because the LLM can return a different number of subtasks (e.g., 7) and slows down test execution. | Medium | Stop Ollama before running `pytest`, or mock the decomposer in the test. *(Open)* |
| **KI-02** | Networking (Tailscale / Windows) | On Windows, new Tailscale nodes may not appear to teammates if **Allow incoming connections** (`--shields-up=false`) is disabled, Windows Firewall blocks port `8000`, or the device is still on a personal tailnet instead of the team tailnet. | Medium | Switch to the shared team tailnet, run `tailscale set --shields-up=false`, and allow inbound traffic from `100.64.0.0/10` in Windows Firewall. *(Documented)* |
| **KI-03** | Local LLM Inference (Ollama) | Running larger local models (e.g., `llama3.1:8b`) on CPU/laptops can take 10–30+ seconds per task, which may trigger client timeouts if multiple tasks queue up. | Low | Pre-warm the model before demos and use lightweight models (e.g., `llama3.2:3b` / `qwen2.5-coder:1.5b`) when RAM is limited. *(Monitoring)* |
| **KI-04** | Backend Dependencies (FastAPI / Starlette) | Running `pytest` emits `StarletteDeprecationWarning` for `httpx` / `HTTP_422_UNPROCESSABLE_ENTITY`. | Low | Cosmetic warning from third-party libraries; does not affect functionality. *(Won't Fix)* |
