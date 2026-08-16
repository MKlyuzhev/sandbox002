import asyncio
import base64
import io
import logging
import time
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar

import httpx
from PIL import Image

from .config import settings

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


class OllamaError(RuntimeError):
    pass


def model_is_loaded(model: str, running: list[str]) -> bool:
    """Return True if ``model`` appears in ``/api/ps`` names (tag or prefix)."""
    needle = model.strip()
    if not needle:
        return False
    for name in running:
        if name == needle or name.startswith(needle):
            return True
    return False


async def list_running_models() -> list[str]:
    """Return names of models currently loaded in Ollama (``GET /api/ps``)."""
    url = f"{settings.ollama_base_url}/api/ps"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            return []
        models = resp.json().get("models") or []
    except httpx.HTTPError:
        return []
    names: list[str] = []
    for item in models:
        for key in ("name", "model"):
            value = item.get(key)
            if value:
                names.append(str(value))
                break
    return names


async def _with_load_status(
    model: str,
    verb: str,
    awaitable: Awaitable[_T],
) -> _T:
    """Await an Ollama call while logging load vs generate so the CLI is not blank."""
    start = time.perf_counter()
    running = await list_running_models()
    loaded = model_is_loaded(model, running)
    if loaded:
        logger.info("%s %s (already in VRAM)", verb, model)
    else:
        logger.info(
            "loading %s into VRAM for %s (first load can take ~30–90s)...",
            model,
            verb,
        )

    stop = asyncio.Event()
    announced_loaded = loaded

    async def _pulse() -> None:
        nonlocal announced_loaded
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=5.0)
                return
            except asyncio.TimeoutError:
                elapsed = time.perf_counter() - start
                now_running = await list_running_models()
                if model_is_loaded(model, now_running):
                    if not announced_loaded:
                        announced_loaded = True
                        logger.info(
                            "%s loaded into VRAM (%.0fs), generating...",
                            model,
                            elapsed,
                        )
                    else:
                        logger.info("%s still generating (%.0fs)...", model, elapsed)
                else:
                    logger.info("still loading %s (%.0fs)...", model, elapsed)

    pulse = asyncio.create_task(_pulse())
    try:
        return await awaitable
    finally:
        stop.set()
        pulse.cancel()
        try:
            await pulse
        except asyncio.CancelledError:
            pass
        logger.info(
            "%s %s done (%.1fs)", verb, model, time.perf_counter() - start
        )


async def embed(text: str) -> list[float]:
    """Return an embedding vector for a single piece of text."""
    url = f"{settings.ollama_base_url}/api/embeddings"
    payload = {"model": settings.ollama_embed_model, "prompt": text}

    async def _post() -> list[float]:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise OllamaError(f"Embedding failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        embedding = data.get("embedding")
        if not embedding:
            raise OllamaError(f"Embedding response missing 'embedding': {data}")
        return embedding

    return await _with_load_status(settings.ollama_embed_model, "embed", _post())


async def chat(system: str, user: str, *, json_mode: bool = False) -> str:
    """Generate a chat completion from the configured LLM."""
    url = f"{settings.ollama_base_url}/api/chat"
    payload: dict = {
        "model": settings.ollama_llm_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"

    async def _post() -> str:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise OllamaError(f"Chat failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        message = data.get("message", {}).get("content")
        if message is None:
            raise OllamaError(f"Chat response missing message content: {data}")
        return message

    return await _with_load_status(settings.ollama_llm_model, "chat", _post())


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

    async def _post() -> str:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code != 200:
            raise OllamaError(f"Vision failed ({resp.status_code}): {resp.text}")
        data = resp.json()
        message = data.get("message", {}).get("content")
        if message is None:
            raise OllamaError(f"Vision response missing message content: {data}")
        return message

    return await _with_load_status(settings.ollama_vision_model, "vision", _post())


async def is_reachable() -> bool:
    url = f"{settings.ollama_base_url}/api/tags"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            return resp.status_code == 200
    except httpx.HTTPError:
        return False
