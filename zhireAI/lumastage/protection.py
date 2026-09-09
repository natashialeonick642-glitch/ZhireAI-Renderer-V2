"""Local ownership and release-integrity checks.

This module deliberately avoids telemetry, machine fingerprinting, API-key
inspection, anti-debugging tricks, and destructive behavior. It only verifies
files inside the installed plugin folder and returns a user-facing warning.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Tuple, Union


PRODUCT_ID = "zhireai.c4d.renderer"
EXPECTED_MANIFEST_SHA256 = "32b9d8699902d521c8fe399f6eb2639bf8e568166c39d24e70b4b22e2257d51d"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_release_path(root: Path, relative_path: str) -> Path:
    relative = Path(str(relative_path or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("完整性清单包含不安全路径")
    target = (root / relative).resolve()
    if os.path.commonpath((str(root.resolve()), str(target))) != str(root.resolve()):
        raise ValueError("完整性清单路径超出插件目录")
    return target


def verify_installation(plugin_root: Union[str, Path]) -> Tuple[bool, str]:
    """Verify local ownership metadata and critical release files.

    The check is intentionally non-blocking. Callers can warn the user while
    still allowing them to recover or reinstall an official package.
    """

    root = Path(plugin_root).resolve()
    try:
        ownership_path = root / "copyright.json"
        manifest_path = root / "integrity.json"
        license_path = root / "LICENSE_zh-CN.txt"
        if not ownership_path.is_file() or not manifest_path.is_file() or not license_path.is_file():
            return False, "插件的版权或完整性文件缺失"

        ownership = json.loads(ownership_path.read_text(encoding="utf-8"))
        if ownership.get("product_id") != PRODUCT_ID:
            return False, "插件的品牌归属信息不匹配"

        if EXPECTED_MANIFEST_SHA256 != "PENDING_RELEASE_MANIFEST":
            if _sha256(manifest_path) != EXPECTED_MANIFEST_SHA256:
                return False, "插件的完整性清单已被修改"

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("product_id") != PRODUCT_ID:
            return False, "插件的完整性清单归属不匹配"
        checksums = manifest.get("sha256")
        if not isinstance(checksums, dict) or not checksums:
            return False, "插件的完整性清单无效"
        for relative_path, expected in sorted(checksums.items()):
            target = _safe_release_path(root, relative_path)
            if not target.is_file():
                return False, "关键文件缺失：{}".format(relative_path)
            if _sha256(target).lower() != str(expected).lower():
                return False, "关键文件已被修改：{}".format(relative_path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return False, "插件完整性校验失败：{}".format(exc)
    return True, "官方发行文件完整"
