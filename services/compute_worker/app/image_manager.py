from pathlib import Path

import requests

from .config import IMAGE_API_URL, IMAGE_CACHE_DIR
from .utils import ensure_dir, run


def cached_image_path(image: dict) -> Path:
    suffix = Path(image.get("filename") or image.get("object_name") or "image.qcow2").suffix or ".qcow2"
    return IMAGE_CACHE_DIR / f"{image['id']}{suffix}"


def download_image(image: dict) -> Path:
    ensure_dir(IMAGE_CACHE_DIR)
    target = cached_image_path(image)
    expected_size = image.get("size")
    if target.exists() and (not expected_size or target.stat().st_size == expected_size):
        return target
    tmp = target.with_suffix(target.suffix + ".part")
    url = f"{IMAGE_API_URL}/images/{image['id']}/download"
    with requests.get(url, stream=True, timeout=60) as response:
        response.raise_for_status()
        with tmp.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    tmp.replace(target)
    return target


def create_overlay(base_image: Path, overlay: Path, disk_gb: int) -> Path:
    overlay.parent.mkdir(parents=True, exist_ok=True)
    if not overlay.exists():
        run(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base_image), str(overlay)])
    run(["qemu-img", "resize", str(overlay), f"{disk_gb}G"])
    return overlay
