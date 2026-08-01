from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from pathlib import PurePosixPath
import shutil
from typing import Protocol

from .config import BackendSettings


@dataclass(frozen=True)
class ArtifactReference:
    provider: str
    key: str
    uri: str
    file_count: int = 0
    byte_count: int = 0


def _safe_tenant_parts(organization_id: str, parts: tuple[str, ...]) -> list[str]:
    if not str(organization_id).strip():
        raise ValueError("Artifact organization_id is required.")
    values = [str(organization_id).strip()]
    values.extend(str(part).strip().replace("\\", "/") for part in parts if str(part).strip())
    safe_parts: list[str] = []
    for raw in values:
        path = PurePosixPath(raw.strip("/"))
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("Artifact tenant keys must be relative paths without traversal segments.")
        safe_parts.extend(path.parts)
    return safe_parts


def safe_join_tenant_path(root: str | Path, organization_id: str, *parts: str) -> Path:
    base = (Path(root) / "organizations" / str(organization_id).strip()).resolve()
    safe_parts = _safe_tenant_parts(organization_id, parts)[1:]
    target = (base / Path(*safe_parts)).resolve()
    if not target.is_relative_to(base):
        raise ValueError("Artifact path escapes the tenant root.")
    return target


def _safe_rmtree(target: Path) -> None:
    resolved = target.resolve()
    if resolved == Path(resolved.anchor) or len(resolved.parts) < 4 or "materialized" not in resolved.parts:
        raise ValueError(f"Refusing to delete unsafe materialized artifact path: {target}")
    shutil.rmtree(resolved)


class ArtifactStorage(Protocol):
    def tenant_key(self, organization_id: str, *parts: str) -> str:
        ...

    def publish_directory(self, source_dir: str | Path, *, organization_id: str, artifact_type: str, artifact_id: str) -> ArtifactReference:
        ...

    def publish_file(self, source_path: str | Path, *, organization_id: str, artifact_type: str, artifact_id: str) -> ArtifactReference:
        ...

    def materialize_directory(self, reference: ArtifactReference, destination: str | Path) -> Path:
        ...


@dataclass(frozen=True)
class LocalArtifactStorage:
    root: Path

    def tenant_key(self, organization_id: str, *parts: str) -> str:
        return str(safe_join_tenant_path(self.root, organization_id, *parts))

    def publish_directory(self, source_dir: str | Path, *, organization_id: str, artifact_type: str, artifact_id: str) -> ArtifactReference:
        source = Path(source_dir)
        key = self.tenant_key(organization_id, artifact_type, artifact_id)
        destination = Path(key)
        if source.exists() and source.resolve() != destination.resolve():
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, dirs_exist_ok=True)
        file_count, byte_count = _directory_stats(destination if destination.exists() else source)
        return ArtifactReference(provider="local", key=key, uri=str(destination), file_count=file_count, byte_count=byte_count)

    def publish_file(self, source_path: str | Path, *, organization_id: str, artifact_type: str, artifact_id: str) -> ArtifactReference:
        source = Path(source_path)
        key = self.tenant_key(organization_id, artifact_type, artifact_id, source.name)
        destination = Path(key)
        if source.exists() and source.resolve() != destination.resolve():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        byte_count = destination.stat().st_size if destination.exists() else (source.stat().st_size if source.exists() else 0)
        return ArtifactReference(provider="local", key=key, uri=str(destination), file_count=1 if byte_count else 0, byte_count=byte_count)

    def materialize_directory(self, reference: ArtifactReference, destination: str | Path) -> Path:
        source = Path(reference.key or reference.uri)
        if not source.exists():
            return source
        target = Path(destination)
        if source.resolve() == target.resolve():
            return target
        if target.exists():
            _safe_rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        return target


@dataclass(frozen=True)
class S3ArtifactStorage:
    bucket: str
    endpoint_url: str | None = None
    access_key_id: str | None = None
    secret_access_key: str | None = None

    def tenant_key(self, organization_id: str, *parts: str) -> str:
        safe_parts = _safe_tenant_parts(organization_id, parts)
        return "/".join(["organizations", *safe_parts])

    def _client(self, *, config=None):
        try:
            import boto3
        except Exception as exc:  # pragma: no cover - dependency checked in production image
            raise RuntimeError("boto3 is required for S3 artifact storage.") from exc
        return boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            config=config,
        )

    def probe_bucket(self, *, timeout_seconds: float = 2.0) -> None:
        """Verify bucket access without creating or mutating remote state."""

        try:
            from botocore.config import Config
        except Exception as exc:  # pragma: no cover - installed with boto3
            raise RuntimeError("botocore is required for S3 artifact storage.") from exc
        timeout = max(0.05, float(timeout_seconds))
        client = self._client(
            config=Config(
                connect_timeout=timeout,
                read_timeout=timeout,
                retries={"max_attempts": 0},
            )
        )
        client.head_bucket(Bucket=self.bucket)

    def _ready_client(self):
        client = self._client()
        try:
            client.head_bucket(Bucket=self.bucket)
        except Exception:
            client.create_bucket(Bucket=self.bucket)
        return client

    def publish_directory(self, source_dir: str | Path, *, organization_id: str, artifact_type: str, artifact_id: str) -> ArtifactReference:
        source = Path(source_dir)
        key_prefix = self.tenant_key(organization_id, artifact_type, artifact_id).rstrip("/")
        file_count = 0
        byte_count = 0
        if source.exists():
            client = self._ready_client()
            for path in source.rglob("*"):
                if not path.is_file():
                    continue
                relative = path.relative_to(source).as_posix()
                object_key = f"{key_prefix}/{relative}"
                client.upload_file(str(path), self.bucket, object_key)
                file_count += 1
                byte_count += path.stat().st_size
        return ArtifactReference(provider="s3", key=key_prefix, uri=f"s3://{self.bucket}/{key_prefix}", file_count=file_count, byte_count=byte_count)

    def publish_file(self, source_path: str | Path, *, organization_id: str, artifact_type: str, artifact_id: str) -> ArtifactReference:
        source = Path(source_path)
        key = self.tenant_key(organization_id, artifact_type, artifact_id, source.name)
        byte_count = source.stat().st_size if source.exists() else 0
        if source.exists():
            self._ready_client().upload_file(str(source), self.bucket, key)
        return ArtifactReference(provider="s3", key=key, uri=f"s3://{self.bucket}/{key}", file_count=1 if byte_count else 0, byte_count=byte_count)

    def materialize_directory(self, reference: ArtifactReference, destination: str | Path) -> Path:
        target = Path(destination)
        if target.exists():
            _safe_rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        prefix = reference.key.rstrip("/") + "/"
        client = self._ready_client()
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                key = item.get("Key")
                if not key or key.endswith("/"):
                    continue
                relative = key[len(prefix):]
                output_path = target / relative
                output_path.parent.mkdir(parents=True, exist_ok=True)
                client.download_file(self.bucket, key, str(output_path))
        return target


def _directory_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def build_artifact_storage(settings: BackendSettings) -> ArtifactStorage:
    if settings.is_production:
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET is required for production artifact storage.")
        return S3ArtifactStorage(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
        )
    return LocalArtifactStorage(root=Path("artifacts"))
