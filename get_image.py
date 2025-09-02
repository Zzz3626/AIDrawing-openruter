import os
import base64
import httpx


async def download_image(url: str, out_path: str = "drawertemp.png") -> str:
    """Download image from a URL to out_path and return the path."""
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content
        import aiofiles
        async with aiofiles.open(out_path, 'wb') as f:
            await f.write(content)
    return os.path.abspath(out_path)


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
    from openai import OpenAI

    api_key = api_key or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set in environment")

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    # Prefer the Images API if supported; fall back to chat if not.
    headers = {}
    if site_url:
        headers["HTTP-Referer"] = site_url
    if site_title:
        headers["X-Title"] = site_title

    # Use chat.completions per OpenRouter's Gemini example; parse for image data/url
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

    # Try to extract a data URL or http(s) URL from the content
    import re
    data_uri_match = re.search(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=]+)", content)
    if data_uri_match:
        img_bytes = base64.b64decode(data_uri_match.group(2))
        with open(out_path, "wb") as f:
            f.write(img_bytes)
        return os.path.abspath(out_path)

    url_match = re.search(r"https?://\S+", content)
    if url_match:
        # Download the referenced URL
        return await download_image(url_match.group(0), out_path)

    # If no image is found, surface the raw content for debugging
    raise RuntimeError(f"模型未返回图片，返回内容: {content[:200]}...")
