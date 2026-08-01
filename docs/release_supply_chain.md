# Release and supply-chain runbook

## Dependency locks

Python 3.12 production and test dependencies are fully resolved with artifact
hashes in `requirements/backend-py312.lock` and
`requirements/test-py312.lock`. Runtime images and CI install these files with
`pip --require-hashes`; the project wheel/editable source is installed
separately with `--no-deps --no-build-isolation`, so it cannot resolve an
unreviewed dependency. `pip-audit`, Bandit, and the observability packages are
part of the test lock and are never installed dynamically in CI.

Regenerate locks only in a dedicated dependency-update change using the
documented compiler version (`uv 0.11.11`) and Python 3.12 resolution target:

```bash
uv pip compile pyproject.toml --extra backend --python-version 3.12 --universal --no-annotate --generate-hashes --output-file requirements/backend-py312.lock
uv pip compile pyproject.toml --extra backend --extra test --python-version 3.12 --universal --no-annotate --generate-hashes --output-file requirements/test-py312.lock
```

Review direct and transitive version changes, license impact, upstream release
notes, hashes, and `pip-audit` output. Run the entire CI gate before merging.
Do not hand-edit versions or hashes. Dependabot/Renovate may propose updates,
but a human must review and regenerate both locks together.

Frontend dependencies are installed only through `npm ci` and the committed
`frontend/package-lock.json`; the production gate includes `npm audit
--omit=dev`.

## CI trust boundary

Every external GitHub Action is pinned to a full commit SHA. Human-readable
version comments are informational; update a pin only after reviewing the
action repository, release notes, and new commit. Workflows declare minimum
permissions, bounded timeouts, and concurrency behavior. Postgres and Redis CI
service images and all Dockerfile base defaults are pinned by multi-architecture
manifest digest.

Required repository controls, which cannot be expressed by files in this
workspace:

- preserve this project in a real Git repository with reviewed history;
- protect the default branch and `v*` tags, prohibit tag deletion/recreation,
  require the CI workflow, and require reviewed pull requests;
- restrict workflow changes with CODEOWNERS and disallow unapproved Actions;
- enable GitHub artifact attestations, GHCR, OIDC, and retention policies;
- enable secret scanning/push protection and configure an incident owner.

This workspace currently has no `.git` metadata. The workflows cannot execute,
establish source provenance, or produce a release until the project is placed
in an authorized remote repository. The application tooling must not silently
initialize or choose that repository.

## Image release

`.github/workflows/release.yml` runs only after its reusable full CI quality
gate succeeds. A protected `vMAJOR.MINOR.PATCH` tag publishes two tags per
component: the human version and `sha-<full-source-commit>`. A manual dispatch
publishes only the SHA tag. The workflow refuses an already existing tag.
Registry tag immutability/protection must also be enabled because registry
administrators can bypass workflow checks.

API and web images are built for `linux/amd64` and `linux/arm64`. The workflow:

1. pushes the digest-pinned result to GHCR;
2. attaches BuildKit SBOM and maximum provenance attestations;
3. emits a reviewable SPDX JSON SBOM;
4. fails on any high or critical Trivy finding, including unfixed findings;
5. keyless-signs the digest with GitHub OIDC and cosign;
6. publishes GitHub build provenance; and
7. retains release identity, SBOM, and scan results as release evidence.

Production deployment must use `repository@sha256:digest`, never either tag.
Verify before rollout:

```bash
cosign verify \
  --certificate-identity-regexp '^https://github.com/OWNER/REPOSITORY/.github/workflows/release.yml@refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$' \
  --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
  ghcr.io/owner/repository/api@sha256:<digest>
```

Adapt the identity to the authorized repository. A separate long-lived signing
key is neither stored nor required. GitHub OIDC, Sigstore/Fulcio/Rekor, GHCR,
and outbound network availability are external release dependencies; outage or
misconfiguration blocks release and must never be bypassed with an unsigned
image.

## Production release checklist

- CI, migration downgrade/re-upgrade, and isolated restore drill passed.
- Source tag is protected, reviewed, and exactly `vMAJOR.MINOR.PATCH`.
- Both image digests match the release evidence and verify with cosign.
- SBOMs are archived; high/critical vulnerability count is zero.
- GitHub/BuildKit provenance refers to the expected source commit and workflow.
- Current Postgres and object-storage recovery points are verified off-account.
- Migration compatibility, rollback/forward-fix, RPO, and RTO are approved.
- Production secrets, external providers, monitoring, alerts, DNS, and TLS are
  independently validated.
- Deployment uses digests and records pre/post health and schema revisions.
