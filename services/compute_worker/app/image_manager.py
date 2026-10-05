from pathlib import Path

import requests

from .config import IMAGE_API_URL, IMAGE_CACHE_DIR
from .utils import ensure_dir, run


def cached_image_path(image: dict) -> Path:
    suffix = Path(image.get("filename") or image.get("object_name") or "image.qcow2").suffix or ".qcow2"
    return IMAGE_CACHE_DIR / f"{image['id']}{suffix}"


def fetch_image_meta(image_ref: str) -> dict:
    """Fetch image metadata from image-api by id or name."""
    response = requests.get(f"{IMAGE_API_URL}/images/{image_ref}", timeout=15)
    response.raise_for_status()
    return response.json()


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


def download_and_prepare_disk(image_ref: str, target: Path) -> Path:
    """Download image by ref (id or name), convert to standalone qcow2 at target path.

    Used by rebuild and rescue operations.  If target already exists it is overwritten.
    """
    image = fetch_image_meta(image_ref)
    cached = download_image(image)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Convert to standalone qcow2 (breaks backing-file chain if image is an overlay)
    run(["qemu-img", "convert", "-O", "qcow2", str(cached), str(target)], timeout=3600)
    return target
