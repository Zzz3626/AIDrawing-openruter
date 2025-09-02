import os
import base64
import httpx
import logging
from pathlib import Path


_logger = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("AIDrawing")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        try:
            base_dir = Path(__file__).parent
        except Exception:
            base_dir = Path(os.getcwd())
        log_dir = base_dir / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_dir / "aidrawing.log", encoding="utf-8")
            fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
            fh.setFormatter(fmt)
            fh.setLevel(logging.DEBUG)
            logger.addHandler(fh)
        except Exception:
            # Fallback to basic config if file handler fails
            logging.basicConfig(level=logging.DEBUG)
    _logger = logger
    return logger


async def download_image(url: str, out_path: str = "drawertemp.png") -> str:
    """Download image from a URL to out_path and return the path."""
    log = _get_logger()
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content
        import aiofiles
        async with aiofiles.open(out_path, 'wb') as f:
            await f.write(content)
    abs_path = os.path.abspath(out_path)
    log.debug(f"Downloaded image to {abs_path} from {url}")
    return abs_path


async def generate_image_with_openrouter(
    prompt: str,
    *,
    out_path: str = "drawertemp.png",
    site_url: str | None = None,
    site_title: str | None = None,
    model: str = "google/gemini-2.5-flash-image-preview:free",
    api_key: str | None = None,
) -> str:
    """
    Generate an image using OpenRouter's API with Gemini 2.5 Flash Image Preview model.

    Returns absolute path to the saved image file.
    """
    log = _get_logger()
    from openai import OpenAI

    env_key = os.getenv("OPENROUTER_API_KEY")
    effective_key = api_key or env_key
    # Fallback: read from local config.json if still missing
    if not effective_key:
        try:
            base_dir = Path(__file__).parent
        except Exception:
            base_dir = Path(os.getcwd())
        cfg_path = base_dir / "config.json"
        def _pick_key(d: dict | None):
            if not isinstance(d, dict):
                return None
            for _k in ("api_key", "apikey", "apiKey", "key", "token", "OPENROUTER_API_KEY"):
                _v = d.get(_k)
                if isinstance(_v, str) and _v.strip():
                    return _v.strip()
            return None
        try:
            if cfg_path.exists():
                import json as _json
                cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
                effective_key = _pick_key(cfg.get("openrouter")) or _pick_key(cfg) or effective_key
                if effective_key:
                    log.info("Resolved API key from local config.json at %s", cfg_path)
        except Exception as _e:
            log.debug("Failed to read local config.json for API key: %s", _e)
    def _mask(k: str | None) -> str:
        if not k:
            return "<empty>"
        if len(k) <= 8:
            return f"{k[0]}***{k[-1]}"
        return f"{k[:4]}***{k[-4:]} (len={len(k)})"
    log.debug(
        "OpenRouter key resolution: passed=%s, env=%s, effective=%s",
        _mask(api_key), _mask(env_key), _mask(effective_key),
    )
    if not effective_key:
        log.warning("No OpenRouter API key available. Set openrouter.api_key or OPENROUTER_API_KEY")
        raise RuntimeError("OPENROUTER_API_KEY is not set in environment")

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=effective_key,
    )

    # Prefer the Images API if supported; fall back to chat if not.
    headers = {}
    if site_url:
        headers["HTTP-Referer"] = site_url
    if site_title:
        headers["X-Title"] = site_title

    # Use chat.completions per OpenRouter's Gemini example; parse for image data/url
    log.debug(f"Calling OpenRouter model={model}, headers={(list(headers.keys()) or None)}")
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        extra_headers=headers or None,
    )
    content = completion.choices[0].message.content or ""
    log.debug("OpenRouter raw content length=%d", len(content))

    # Try to extract a data URL or http(s) URL from the content
    import re
    data_uri_match = re.search(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", content)
    if data_uri_match:
        img_bytes = base64.b64decode(data_uri_match.group(2))
        with open(out_path, "wb") as f:
            f.write(img_bytes)
        abs_path = os.path.abspath(out_path)
        log.info(f"Saved image from data URI to {abs_path}")
        return abs_path

    url_match = re.search(r"https?://\S+", content)
    if url_match:
        # Download the referenced URL
        url = url_match.group(0)
        log.info(f"Downloading image from URL: {url}")
        return await download_image(url, out_path)

    # If no image is found, surface the raw content for debugging
    log.warning("Model did not return an image. First 200 chars: %s", content[:200])
    raise RuntimeError(f"模型未返回图片，返回内容: {content[:200]}...")
