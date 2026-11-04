import httpx
import logging

logger = logging.getLogger(__name__)

# System prompt templates tailored for each task type (Strictly in English)
TASK_PROMPTS = {
    "backend": (
        "You are an expert Backend Engineer. Write clean, efficient, production-ready code "
        "(e.g., Python/FastAPI) for the given task. Output ONLY executable code blocks "
        "without conversational filler or markdown explanations."
    ),
    "frontend": (
        "You are an expert Frontend Engineer. Write clean, responsive UI components or code "
        "based on the specifications. Output ONLY code."
    ),
    "database": (
        "You are a Database Specialist. Write optimized SQL queries, migrations, or database schemas. "
        "Output ONLY raw SQL or schema definitions."
    ),
    "testing": (
        "You are a QA Automation Engineer. Write comprehensive unit or integration tests. "
        "Output ONLY valid test code."
    ),
    "docs": (
        "You are a Technical Writer. Write clear, concise Markdown documentation."
    )
}

async def execute_agent_task(model_name: str, task_type: str, task_title: str, task_description: str, base_url: str = "http://localhost:11434") -> str:
    """
    Executes the given task via local Ollama API using the selected agent's model 
    and returns clean, usable code output.
    """
    system_prompt = TASK_PROMPTS.get(task_type.lower(), TASK_PROMPTS["backend"])
    
    user_prompt = f"Task Title: {task_title}\nDescription: {task_description}"

    payload = {
        "model": model_name,
        "prompt": f"{system_prompt}\n\n{user_prompt}",
        "stream": False
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(f"{base_url}/api/generate", json=payload)
            response.raise_for_status()
            data = response.json()
            return data.get("response", "").strip()
    except Exception as error_message:
        logger.error(f"Execution failed for model {model_name}: {str(error_message)}")
        return f"Error executing task with model {model_name}: {str(error_message)}"