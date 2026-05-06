from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import BackendSettings


class ArtifactStorage(Protocol):
    def tenant_key(self, organization_id: str, *parts: str) -> str:
        ...


@dataclass(frozen=True)
class LocalArtifactStorage:
    root: Path

    def tenant_key(self, organization_id: str, *parts: str) -> str:
        safe_parts = [part.strip("/\\").replace("\\", "/") for part in parts if part]
        return str(self.root / "organizations" / organization_id / Path(*safe_parts))


@dataclass(frozen=True)
class S3ArtifactStorage:
    bucket: str

    def tenant_key(self, organization_id: str, *parts: str) -> str:
        safe_parts = [part.strip("/\\").replace("\\", "/") for part in parts if part]
        return "/".join(["organizations", organization_id, *safe_parts])


def build_artifact_storage(settings: BackendSettings) -> ArtifactStorage:
    if settings.is_production:
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for production artifact storage.")
        return S3ArtifactStorage(bucket=settings.s3_bucket)
    return LocalArtifactStorage(root=Path("artifacts"))
