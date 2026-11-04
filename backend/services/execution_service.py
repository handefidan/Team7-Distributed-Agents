import os
import re
import logging
import httpx

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "300"))

TASK_PROMPTS = {
    "backend": (
        "You are an expert Backend Engineer. Write clean, production-ready Python/FastAPI code "
        "for the given task. Output ONLY a single executable code block. "
        "No explanations, no conversational text."
    ),
    "frontend": (
        "You are an expert Frontend Engineer. Write clean, responsive UI code for the given task. "
        "Output ONLY a single code block. No explanations."
    ),
    "database": (
        "You are a Database Specialist. Write optimized SQL queries, migrations, or schemas. "
        "Output ONLY a single SQL code block. No explanations."
    ),
    "testing": (
        "You are a QA Automation Engineer. Write comprehensive pytest unit or integration tests. "
        "Output ONLY a single code block. No explanations."
    ),
    "docs": "You are a Technical Writer. Write clear, concise Markdown documentation.",
}

# Bu türlerde çıktı kod olarak temizlenir; docs ise Markdown olduğu gibi kalır.
CODE_TASK_TYPES = {"backend", "frontend", "database", "testing"}

CODE_BLOCK_RE = re.compile(r"```[\w+-]*\n(.*?)```", re.DOTALL)


class AgentExecutionError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def extract_code(text: str) -> str:
    """Modelin cevabından kod bloklarını çıkarır; blok yoksa metni olduğu gibi döndürür."""
    blocks = CODE_BLOCK_RE.findall(text)
    if blocks:
        return "\n\n".join(b.strip() for b in blocks)
    # Kapanmamış blok (model yarıda kesilmiş olabilir)
    if text.lstrip().startswith("```"):
        return re.sub(r"^```[\w+-]*\n?", "", text.strip()).strip()
    return text.strip()


async def execute_agent_task(
    model_name: str,
    task_type: str,
    task_title: str,
    task_description: str,
    base_url: str = OLLAMA_BASE_URL,
) -> str:
    task_type = task_type.lower()
    if task_type not in TASK_PROMPTS:
        raise AgentExecutionError(f"Unknown task type: {task_type}", status_code=422)

    payload = {
        "model": model_name,
        "system": TASK_PROMPTS[task_type],
        "prompt": f"Task Title: {task_title}\nDescription: {task_description}",
        "stream": False,
        "options": {"temperature": 0.2},
    }

    try:
        timeout = httpx.Timeout(OLLAMA_TIMEOUT, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{base_url}/api/generate", json=payload)
            response.raise_for_status()
            raw = response.json().get("response", "").strip()
    except httpx.ConnectError:
        raise AgentExecutionError("Ollama is not reachable. Is `ollama serve` running?", 503)
    except httpx.TimeoutException:
        raise AgentExecutionError(f"Model {model_name} timed out.", 504)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise AgentExecutionError(
                f"Model '{model_name}' not found. Run: ollama pull {model_name}", 404
            )
        raise AgentExecutionError(f"Ollama error: {e.response.status_code}", 502)
    except Exception as e:
        logger.exception("Unexpected failure for model %s", model_name)
        raise AgentExecutionError(f"Unexpected error: {e}", 500)

    if not raw:
        raise AgentExecutionError(f"Model {model_name} returned an empty response.", 502)

    return extract_code(raw) if task_type in CODE_TASK_TYPES else raw
