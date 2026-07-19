import base64
import io
import logging
from pathlib import Path

import httpx
from PIL import Image

from .config import settings

logger = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


async def embed(text: str) -> list[float]:
    """Return an embedding vector for a single piece of text."""
    url = f"{settings.ollama_base_url}/api/embeddings"
    payload = {"model": settings.ollama_embed_model, "prompt": text}
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise OllamaError(f"Embedding failed ({resp.status_code}): {resp.text}")
        data = resp.json()
    embedding = data.get("embedding")
    if not embedding:
        raise OllamaError(f"Embedding response missing 'embedding': {data}")
    return embedding


async def chat(system: str, user: str) -> str:
    """Generate a chat completion from the configured LLM."""
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": settings.ollama_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise OllamaError(f"Chat failed ({resp.status_code}): {resp.text}")
        data = resp.json()
    message = data.get("message", {}).get("content")
    if message is None:
        raise OllamaError(f"Chat response missing message content: {data}")
    return message


async def describe_image(image_path: str, prompt: str | None = None) -> str:
    """Describe an image using the configured Ollama vision model."""
    path = Path(image_path)
    if not path.is_file():
        raise OllamaError(f"Image not found: {image_path}")

    try:
        with Image.open(path) as img:
            rgb = img.convert("RGB")
            buf = io.BytesIO()
            rgb.save(buf, format="PNG")
            image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        raise OllamaError(f"Could not read image {image_path}: {exc}") from exc
    url = f"{settings.ollama_base_url}/api/chat"
    payload = {
        "model": settings.ollama_vision_model,
        "messages": [
            {
                "role": "user",
                "content": prompt or settings.figure_caption_prompt,
                "images": [image_b64],
            }
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise OllamaError(f"Vision failed ({resp.status_code}): {resp.text}")
        data = resp.json()
    message = data.get("message", {}).get("content")
    if message is None:
        raise OllamaError(f"Vision response missing message content: {data}")
    return message


async def is_reachable() -> bool:
    url = f"{settings.ollama_base_url}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
