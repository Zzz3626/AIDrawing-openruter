import os
import base64
import httpx
import logging
from pathlib import Path
import json
import re


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
    size: str | None = "1024x1024",
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

    # Prefer Responses API with explicit image modality; fall back to chat.
    headers = {}
    if site_url:
        headers["HTTP-Referer"] = site_url
    if site_title:
        headers["X-Title"] = site_title

    def _to_plain(obj):
        try:
            if hasattr(obj, "model_dump"):
                return obj.model_dump()
            if hasattr(obj, "model_dump_json"):
                return json.loads(obj.model_dump_json())
            if hasattr(obj, "to_dict"):
                return obj.to_dict()
            if hasattr(obj, "dict"):
                return obj.dict()
        except Exception:
            pass
        return obj

    def _iter_nodes(o):
        stack = [o]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                yield cur
                for v in cur.values():
                    stack.append(v)
            elif isinstance(cur, (list, tuple)):
                for v in cur:
                    stack.append(v)
            else:
                # non-iterable leaf
                continue

    async def _save_from_any(obj) -> str | None:
        plain = _to_plain(obj)
        # 1) Look for explicit image fields first (b64 or URL)
        for node in _iter_nodes(plain):
            if not isinstance(node, dict):
                continue
            # OpenAI Responses API style: {"type":"output_image", "image": {"b64":..., "mime_type":...}} or variants
            if node.get("type") in {"output_image", "image", "image_url"}:
                img = node.get("image") or node.get("image_url") or node
                # Direct string URL
                if isinstance(img, str) and img.startswith("http"):
                    log.info(f"Downloading image from URL (string): {img}")
                    return await download_image(img, out_path)
                if isinstance(img, dict):
                    # Base64 variants
                    for k in ("b64_json", "b64", "base64", "data"):
                        b64v = img.get(k)
                        if isinstance(b64v, str) and len(b64v) > 64:
                            try:
                                # allow possible data:image/...;base64, prefix
                                if b64v.startswith("data:image"):
                                    comma = b64v.find(",")
                                    if comma != -1:
                                        b64v = b64v[comma + 1 :]
                                data = base64.b64decode(b64v)
                                with open(out_path, "wb") as f:
                                    f.write(data)
                                abs_path = os.path.abspath(out_path)
                                log.info(f"Saved image b64 to {abs_path}")
                                return abs_path
                            except Exception as _e:
                                log.debug("Base64 decode candidate failed: %s", _e)
                    # Nested source shapes e.g. {source:{data,url,media_type}}
                    src = img.get("source") if isinstance(img.get("source"), dict) else None
                    if src:
                        for k in ("b64_json", "b64", "base64", "data"):
                            b64v = src.get(k)
                            if isinstance(b64v, str) and len(b64v) > 64:
                                try:
                                    if b64v.startswith("data:image"):
                                        comma = b64v.find(",")
                                        if comma != -1:
                                            b64v = b64v[comma + 1 :]
                                    data = base64.b64decode(b64v)
                                    with open(out_path, "wb") as f:
                                        f.write(data)
                                    abs_path = os.path.abspath(out_path)
                                    log.info(f"Saved image b64 (source) to {abs_path}")
                                    return abs_path
                                except Exception as _e:
                                    log.debug("Base64 (source) decode failed: %s", _e)
                        url = src.get("url")
                        if isinstance(url, str) and url.startswith("http"):
                            log.info(f"Downloading image from URL (source): {url}")
                            return await download_image(url, out_path)
                    # URL variants
                    url = img.get("url") or img.get("image_url") or img.get("link")
                    if isinstance(url, str) and url.startswith("http"):
                        log.info(f"Downloading image from URL: {url}")
                        return await download_image(url, out_path)
            # Some providers return {"mime_type":"image/png","url":"..."}
            if (node.get("mime_type", "").startswith("image/") and isinstance(node.get("url"), str)):
                url = node["url"]
                if url.startswith("http"):
                    log.info(f"Downloading image from URL: {url}")
                    return await download_image(url, out_path)
            # Attachments style: {attachments:[{mime_type, url, data}]}
            if isinstance(node.get("attachments"), list):
                for att in node.get("attachments"):
                    if not isinstance(att, dict):
                        continue
                    mt = att.get("mime_type") or att.get("mime") or ""
                    if isinstance(mt, str) and mt.startswith("image/"):
                        # base64
                        for k in ("b64_json", "b64", "base64", "data"):
                            b64v = att.get(k)
                            if isinstance(b64v, str) and len(b64v) > 64:
                                try:
                                    if b64v.startswith("data:image"):
                                        comma = b64v.find(",")
                                        if comma != -1:
                                            b64v = b64v[comma + 1 :]
                                    data = base64.b64decode(b64v)
                                    with open(out_path, "wb") as f:
                                        f.write(data)
                                    abs_path = os.path.abspath(out_path)
                                    log.info(f"Saved image b64 (attachment) to {abs_path}")
                                    return abs_path
                                except Exception as _e:
                                    log.debug("Attachment base64 decode failed: %s", _e)
                        u = att.get("url") or att.get("image_url")
                        if isinstance(u, str) and u.startswith("http"):
                            log.info(f"Downloading image from URL (attachment): {u}")
                            return await download_image(u, out_path)

        # 2) Check message content string(s) for data URL or http URL
        try:
            # Accept assistant message content in both string and parts array forms
            if isinstance(obj, dict):
                content_val = obj.get("content")
            else:
                content_val = getattr(obj, "content", None)
        except Exception:
            content_val = None

        def _try_extract_from_text(s: str) -> str | None:
            data_uri_match = re.search(r"data:image/(png|jpe?g|webp|gif);base64,([A-Za-z0-9+/=]+)", s, flags=re.IGNORECASE)
            if data_uri_match:
                img_bytes = base64.b64decode(data_uri_match.group(2))
                with open(out_path, "wb") as f:
                    f.write(img_bytes)
                abs_path = os.path.abspath(out_path)
                log.info(f"Saved image from data URI to {abs_path}")
                return abs_path
            url_match = re.search(r"https?://\S+", s)
            if url_match:
                url = url_match.group(0)
                log.info(f"Downloading image from URL: {url}")
                return None  # Let caller handle download to avoid duplicate writes
            return None

        if isinstance(content_val, str):
            maybe = _try_extract_from_text(content_val)
            if isinstance(maybe, str):
                return maybe
            # If a URL was detected, try download here
            url_match = re.search(r"https?://\S+", content_val)
            if url_match:
                return await download_image(url_match.group(0), out_path)
        elif isinstance(content_val, list):
            for part in content_val:
                if isinstance(part, dict):
                    t = part.get("type")
                    if t in {"image_url", "image", "output_image"}:
                        v = part.get("image_url") or part.get("image")
                        # direct string
                        if isinstance(v, str) and v.startswith("http"):
                            return await download_image(v, out_path)
                        # object with url
                        if isinstance(v, dict):
                            url = v.get("url") or v.get("image_url") or v.get("link")
                            if isinstance(url, str) and url.startswith("http"):
                                return await download_image(url, out_path)
                            # nested source
                            src = v.get("source") if isinstance(v.get("source"), dict) else None
                            if src:
                                url = src.get("url")
                                if isinstance(url, str) and url.startswith("http"):
                                    return await download_image(url, out_path)
                                for k in ("b64_json", "b64", "base64", "data"):
                                    b64v = src.get(k)
                                    if isinstance(b64v, str) and len(b64v) > 64:
                                        try:
                                            if b64v.startswith("data:image"):
                                                comma = b64v.find(",")
                                                if comma != -1:
                                                    b64v = b64v[comma + 1 :]
                                            with open(out_path, "wb") as f:
                                                f.write(base64.b64decode(b64v))
                                            return os.path.abspath(out_path)
                                        except Exception as _e:
                                            log.debug("Part source base64 decode failed: %s", _e)
                    txt = part.get("text") or part.get("input_text") or ""
                    if isinstance(txt, str) and txt:
                        maybe = _try_extract_from_text(txt)
                        if isinstance(maybe, str):
                            return maybe
                        u = re.search(r"https?://\S+", txt)
                        if u:
                            return await download_image(u.group(0), out_path)

        return None

    # First attempt: chat.completions mirroring the simple sample flow
    log.debug(f"Calling OpenRouter Chat Completions model={model}, headers={(list(headers.keys()) or None)}")
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are an image generator. Return exactly one data URI in the form "
                    "data:image/png;base64,<BASE64>. Do not include any extra text."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.8,
        max_tokens=4000,
        extra_headers=headers or None,
    )
    # Try to parse structured parts first
    try:
        msg = completion.choices[0].message
    except Exception:
        msg = None
    if msg is not None:
        saved = await _save_from_any(_to_plain(msg))
        if isinstance(saved, str):
            return saved

    # Last resort: treat message content as plain text
    content = (getattr(getattr(completion.choices[0], "message", {}), "content", "") or "")
    if isinstance(content, str):
        log.debug("OpenRouter raw content length=%d", len(content))
        # Match any image mime-type like the official sample (broader than png/jpeg)
        data_uri_match = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", content, flags=re.IGNORECASE)
        if data_uri_match:
            img_bytes = base64.b64decode(data_uri_match.group(1))
            with open(out_path, "wb") as f:
                f.write(img_bytes)
            abs_path = os.path.abspath(out_path)
            log.info(f"Saved image from data URI to {abs_path}")
            return abs_path
        url_match = re.search(r"https?://\S+", content)
        if url_match:
            url = url_match.group(0)
            log.info(f"Downloading image from URL: {url}")
            return await download_image(url, out_path)

    # Second attempt: Responses API with image modality (as a fallback)
    try:
        log.debug(f"Calling OpenRouter Responses API model={model}, headers={(list(headers.keys()) or None)} size={size}")
        responses = getattr(client, "responses", None)
        if responses is not None and hasattr(responses, "create"):
            # Simpler input to align with generic examples
            resp = responses.create(
                model=model,
                input=prompt,
                modalities=["image"],
                extra_headers=headers or None,
                extra_body={"image": {"size": size}} if size else None,
                max_output_tokens=4000,
                temperature=0.8,
            )
            saved = await _save_from_any(resp)
            if isinstance(saved, str):
                return saved
            # Broad regex over the entire response JSON as last-ditch
            try:
                plain = _to_plain(resp)
                txt = json.dumps(plain, ensure_ascii=False)
                m = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", txt, flags=re.IGNORECASE)
                if m:
                    with open(out_path, "wb") as f:
                        f.write(base64.b64decode(m.group(1)))
                    return os.path.abspath(out_path)
            except Exception as _e:
                log.debug("Responses JSON scan failed: %s", _e)
    except Exception as e:
        log.debug("Responses API call failed: %s", e)

    # If no image is found, dump compact JSON for debugging and surface an error
    try:
        debug_obj = _to_plain(completion)
        debug_txt = json.dumps(debug_obj, ensure_ascii=False)[:200]
    except Exception:
        debug_txt = (content if isinstance(content, str) else "")[:200]
    log.warning("Model did not return an image. First 200 chars: %s", debug_txt)
    # Persist full response for troubleshooting
    try:
        base_dir = Path(__file__).parent if Path(__file__).exists() else Path(os.getcwd())
        dbg_path = base_dir / "logs" / "last_openrouter_response.json"
        dbg_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dbg_path, "w", encoding="utf-8") as f:
            try:
                json.dump(_to_plain(completion), f, ensure_ascii=False)
            except Exception:
                f.write(str(completion))
        log.info("Saved debug response to %s", dbg_path)
    except Exception:
        pass
    raise RuntimeError(f"模型未返回图片，返回内容: {debug_txt}...")
