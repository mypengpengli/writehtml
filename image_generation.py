"""OpenAI-compatible image generation and safe local portrait storage."""
import base64
import binascii
import os
import re
import shutil
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx

import config


class ImageGenerationError(Exception):
    pass


def generation_url(base_url):
    value = (base_url or "").strip().rstrip("/")
    if not value:
        raise ImageGenerationError("请先配置生图服务 Base URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ImageGenerationError("生图服务 Base URL 格式不正确")
    if value.endswith("/images/generations"):
        return value
    return value + "/images/generations"


def _provider_error(response):
    try:
        payload = response.json()
    except Exception:
        payload = None
    message = ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("code") or ""
        elif isinstance(error, str):
            message = error
        message = message or payload.get("message") or payload.get("detail") or ""
    if not message:
        message = re.sub(r"<[^>]+>", " ", response.text or "")
        message = re.sub(r"\s+", " ", message).strip()
    message = str(message)[:400]
    return f"生图服务返回 {response.status_code}" + (f"：{message}" if message else "")


def _decode_data_url(value):
    match = re.fullmatch(r"data:([^;,]+)?;base64,(.+)", value or "", re.DOTALL)
    if not match:
        return None
    try:
        return base64.b64decode(match.group(2), validate=True), (match.group(1) or "")
    except (ValueError, binascii.Error):
        raise ImageGenerationError("生图服务返回了无效的图片数据")


async def generate_image(base_url, api_key, model, prompt, size="1024x1024"):
    """Return (image_bytes, provider_content_type) from an OpenAI-compatible endpoint."""
    model = (model or "").strip()
    prompt = (prompt or "").strip()
    if not api_key:
        raise ImageGenerationError("请先配置生图服务 API Key")
    if not model:
        raise ImageGenerationError("请先配置生图模型 ID")
    if not prompt:
        raise ImageGenerationError("角色形象提示词不能为空")
    payload = {"model": model[:200], "prompt": prompt[:8000], "n": 1}
    if size:
        payload["size"] = size[:32]
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(120.0, connect=20.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            response = await client.post(generation_url(base_url), headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise ImageGenerationError(f"无法连接生图服务：{exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            raise ImageGenerationError(_provider_error(response))
        try:
            result = response.json()
        except Exception as exc:
            raise ImageGenerationError("生图服务没有返回 JSON") from exc
        data = result.get("data") if isinstance(result, dict) else None
        item = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
        if not item:
            raise ImageGenerationError("生图服务返回中没有图片")
        encoded = item.get("b64_json")
        if encoded:
            try:
                return base64.b64decode(encoded, validate=True), ""
            except (ValueError, binascii.Error) as exc:
                raise ImageGenerationError("生图服务返回了无效的 base64 图片") from exc
        image_url = item.get("url")
        data_image = _decode_data_url(image_url)
        if data_image:
            return data_image
        parsed = urlparse(image_url or "")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ImageGenerationError("生图服务返回中没有可用的图片地址")
        try:
            downloaded = await client.get(image_url)
        except httpx.HTTPError as exc:
            raise ImageGenerationError("图片已生成，但下载失败") from exc
        if downloaded.status_code >= 400:
            raise ImageGenerationError(f"图片已生成，但下载返回 {downloaded.status_code}")
        return downloaded.content, downloaded.headers.get("content-type", "")


def _image_kind(data, content_type=""):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif"
    if len(data) >= 12 and data[4:12] in {b"ftypavif", b"ftypavis"}:
        return "avif", "image/avif"
    raise ImageGenerationError(f"生图服务返回的不是受支持的图片（{content_type or '未知类型'}）")


def save_image(user_id, entity_id, data, content_type=""):
    if not data:
        raise ImageGenerationError("生图服务返回了空图片")
    if len(data) > config.ENTITY_IMAGE_MAX_BYTES:
        raise ImageGenerationError("生成的图片超过存储大小限制")
    ext, mime = _image_kind(data, content_type)
    root = Path(config.ENTITY_IMAGE_STORAGE_DIR).resolve()
    folder = root / f"user-{int(user_id)}" / f"entity-{int(entity_id)}"
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.{ext}"
    target = folder / filename
    target.write_bytes(data)
    return target.relative_to(root).as_posix(), mime


def resolve_image_path(relative_path):
    root = Path(config.ENTITY_IMAGE_STORAGE_DIR).resolve()
    target = (root / (relative_path or "")).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ImageGenerationError("图片路径无效") from exc
    return target


def remove_image(relative_path):
    if not relative_path:
        return
    try:
        target = resolve_image_path(relative_path)
        if target.is_file():
            target.unlink()
        parent = target.parent
        root = Path(config.ENTITY_IMAGE_STORAGE_DIR).resolve()
        if parent != root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except (OSError, ImageGenerationError):
        pass


def remove_user_images(user_id):
    root = Path(config.ENTITY_IMAGE_STORAGE_DIR).resolve()
    target = (root / f"user-{int(user_id)}").resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)


def image_media_type(path):
    suffix = Path(path).suffix.lower()
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif"}.get(suffix, "application/octet-stream")
