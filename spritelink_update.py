from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


GITHUB_API_VERSION = "2022-11-28"
MAX_INSTALLER_BYTES = 250 * 1024 * 1024
VERSION_PATTERN = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
)
SHA256_PATTERN = re.compile(
    r"^([0-9a-fA-F]{64})(?:\s+\*?(.+?))?\s*$"
)


class UpdateError(RuntimeError):
    pass


def _requests_module() -> Any:
    try:
        import requests
    except ImportError as exc:
        raise UpdateError("The requests package is unavailable.") from exc
    return requests


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag_name: str
    installer_url: str
    checksum_url: str
    page_url: str
    notes: str

    @property
    def installer_name(self) -> str:
        return f"SpriteLink-Setup-{self.version}.exe"


def parse_semantic_version(value: str) -> tuple[int, int, int] | None:
    match = VERSION_PATTERN.fullmatch(str(value).strip())
    if match is None:
        return None
    return tuple(int(component) for component in match.groups())


def release_is_newer(current_version: str, release_version: str) -> bool:
    current = parse_semantic_version(current_version)
    release = parse_semantic_version(release_version)
    return current is not None and release is not None and release > current


def _require_https_url(value: Any, description: str) -> str:
    url = str(value or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise UpdateError(f"The release has an invalid {description} URL.")
    return url


def release_from_github_payload(payload: Any) -> ReleaseInfo:
    if not isinstance(payload, dict):
        raise UpdateError("GitHub returned an invalid release response.")

    tag_name = str(payload.get("tag_name", "")).strip()
    parsed_version = parse_semantic_version(tag_name)
    if parsed_version is None:
        raise UpdateError("The latest release tag is not a valid version.")
    version = ".".join(str(component) for component in parsed_version)
    installer_name = f"SpriteLink-Setup-{version}.exe"
    checksum_name = f"{installer_name}.sha256"

    assets = payload.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("GitHub returned an invalid release asset list.")

    asset_urls: dict[str, str] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name", ""))
        if name not in (installer_name, checksum_name):
            continue
        asset_urls[name] = _require_https_url(
            asset.get("browser_download_url"),
            name,
        )

    missing_assets = [
        name
        for name in (installer_name, checksum_name)
        if name not in asset_urls
    ]
    if missing_assets:
        raise UpdateError(
            "The latest release is missing: " + ", ".join(missing_assets)
        )

    return ReleaseInfo(
        version=version,
        tag_name=tag_name,
        installer_url=asset_urls[installer_name],
        checksum_url=asset_urls[checksum_name],
        page_url=_require_https_url(payload.get("html_url"), "release page"),
        notes=str(payload.get("body", "") or ""),
    )


def fetch_latest_release(
    repository: str,
    *,
    current_version: str,
    timeout_seconds: float = 15.0,
) -> ReleaseInfo:
    requests_module = _requests_module()
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": f"SpriteLink/{current_version}",
    }
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    try:
        response = requests_module.get(
            url,
            headers=headers,
            timeout=timeout_seconds,
        )
        if response.status_code == 404:
            raise UpdateError(
                "No public SpriteLink release could be found on GitHub."
            )
        response.raise_for_status()
        return release_from_github_payload(response.json())
    except UpdateError:
        raise
    except (requests_module.RequestException, ValueError) as exc:
        raise UpdateError(f"Could not check GitHub for updates: {exc}") from exc


def parse_sha256_checksum(text: str, expected_filename: str) -> str:
    for line in str(text).splitlines():
        match = SHA256_PATTERN.fullmatch(line.strip())
        if match is None:
            continue
        checksum = match.group(1).lower()
        listed_filename = (match.group(2) or "").strip()
        if (
            listed_filename
            and Path(listed_filename).name != expected_filename
        ):
            continue
        return checksum
    raise UpdateError("The release checksum file is invalid.")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_release_installer(
    release: ReleaseInfo,
    destination_directory: Path,
) -> Path:
    requests_module = _requests_module()
    destination_directory.mkdir(parents=True, exist_ok=True)
    installer_path = destination_directory / release.installer_name
    partial_path = installer_path.with_suffix(".exe.part")

    for old_path in destination_directory.glob("SpriteLink-Setup-*.exe*"):
        if old_path in (installer_path, partial_path):
            continue
        try:
            old_path.unlink()
        except OSError:
            pass

    try:
        checksum_response = requests_module.get(
            release.checksum_url,
            headers={"User-Agent": f"SpriteLink/{release.version}"},
            timeout=(10, 30),
        )
        checksum_response.raise_for_status()
        if len(checksum_response.content) > 16 * 1024:
            raise UpdateError("The release checksum file is unexpectedly large.")
        expected_checksum = parse_sha256_checksum(
            checksum_response.text,
            release.installer_name,
        )

        if (
            installer_path.is_file()
            and installer_path.stat().st_size <= MAX_INSTALLER_BYTES
            and _file_sha256(installer_path) == expected_checksum
        ):
            return installer_path

        partial_path.unlink(missing_ok=True)
        with requests_module.get(
            release.installer_url,
            headers={"User-Agent": f"SpriteLink/{release.version}"},
            timeout=(10, 60),
            stream=True,
        ) as installer_response:
            installer_response.raise_for_status()
            final_url = str(installer_response.url)
            if urlsplit(final_url).scheme != "https":
                raise UpdateError("GitHub redirected to an insecure download.")
            content_length = installer_response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    announced_size = int(content_length)
                except (TypeError, ValueError):
                    announced_size = 0
                if announced_size > MAX_INSTALLER_BYTES:
                    raise UpdateError("The update installer is unexpectedly large.")

            digest = hashlib.sha256()
            downloaded_bytes = 0
            with partial_path.open("wb") as destination:
                for chunk in installer_response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > MAX_INSTALLER_BYTES:
                        raise UpdateError(
                            "The update installer is unexpectedly large."
                        )
                    destination.write(chunk)
                    digest.update(chunk)

        if downloaded_bytes == 0:
            raise UpdateError("GitHub returned an empty update installer.")
        if digest.hexdigest() != expected_checksum:
            raise UpdateError("The downloaded update failed SHA-256 verification.")

        os.replace(partial_path, installer_path)
        return installer_path
    except UpdateError:
        partial_path.unlink(missing_ok=True)
        raise
    except (OSError, requests_module.RequestException) as exc:
        partial_path.unlink(missing_ok=True)
        raise UpdateError(f"Could not download the update: {exc}") from exc
