from __future__ import annotations

from pathlib import Path
import re

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def _workflow(name: str) -> tuple[str, dict]:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)
    return text, parsed


def test_every_external_action_is_immutably_pinned() -> None:
    pattern = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)", re.MULTILINE)
    for path in WORKFLOWS.glob("*.yml"):
        for reference in pattern.findall(path.read_text(encoding="utf-8")):
            if reference.startswith("./"):
                continue
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", reference), (path.name, reference)


def test_ci_uses_hash_locked_installs_and_least_privilege_gates() -> None:
    text, workflow = _workflow("ci.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is True
    assert workflow["jobs"]["test"]["timeout-minutes"] <= 60
    assert "pip install --require-hashes -r requirements/test-py312.lock" in text
    assert "pip install --no-deps --no-build-isolation -e ." in text
    assert "pip_audit --strict --require-hashes" in text
    assert "pip install bandit" not in text
    assert "alembic downgrade -1" in text
    assert "test_postgres_restore_drill.py" in text


def test_release_requires_quality_gate_and_emits_verifiable_evidence() -> None:
    text, workflow = _workflow("release.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["concurrency"]["cancel-in-progress"] is False
    publish = workflow["jobs"]["publish"]
    assert publish["needs"] == "quality-gate"
    assert publish["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    for required in (
        "linux/amd64,linux/arm64",
        "provenance: mode=max",
        "sbom: true",
        "anchore/sbom-action@",
        "aquasecurity/trivy-action@",
        "cosign sign --yes",
        "actions/attest-build-provenance@",
        "sha-${GITHUB_SHA}",
        "^v[0-9]+\\.[0-9]+\\.[0-9]+$",
    ):
        assert required in text


def test_runtime_base_defaults_are_multiarch_digest_pinned() -> None:
    for name in ("Dockerfile.api", "Dockerfile.web"):
        text = (ROOT / name).read_text(encoding="utf-8")
        defaults = re.findall(r"^ARG\s+\w+_IMAGE=([^\s]+)$", text, re.MULTILINE)
        assert defaults
        assert all(re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", value) for value in defaults)


def test_python_locks_are_hash_complete_and_preserve_observability() -> None:
    for name in ("backend-py312.lock", "test-py312.lock"):
        text = (ROOT / "requirements" / name).read_text(encoding="utf-8")
        requirements = [line for line in text.splitlines() if line and not line.startswith(("#", " "))]
        assert requirements
        assert all("==" in line for line in requirements)
        assert text.count("--hash=sha256:") >= len(requirements)
        assert "prometheus-client==" in text
        assert "opentelemetry-sdk==" in text
