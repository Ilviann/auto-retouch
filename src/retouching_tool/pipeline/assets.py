"""Local-only checkpoint identity checks. No downloads or inferred licenses."""

from dataclasses import dataclass
import hashlib
from pathlib import Path, PureWindowsPath
import re
import traceback


@dataclass(frozen=True)
class ModelFile:
    relative_path: str
    sha256: str

    def __post_init__(self):
        path = PureWindowsPath(self.relative_path)
        if (not self.relative_path or path.is_absolute() or path.drive or path.root
                or ".." in path.parts or ":" in self.relative_path
                or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)):
            raise ValueError("Model files require a safe relative path and lowercase SHA-256")


@dataclass(frozen=True)
class ModelManifest:
    ref: str
    version: str
    files: tuple[ModelFile, ...]
    backends: tuple[str, ...]
    publisher_url: str
    commercial_use_evidence: str

    def __post_init__(self):
        if not self.files or not self.version or not self.commercial_use_evidence or not self.publisher_url.startswith("https://"):
            raise ValueError("Model manifests require files, version, publisher page, and commercial-use evidence")


@dataclass(frozen=True)
class AssetIssue:
    code: str
    message: str
    path: Path | None = None
    technical_cause: str | None = None


class ModelCatalog:
    def __init__(self, manifests=()):
        self._models = {}
        for manifest in manifests:
            if manifest.ref in self._models:
                raise ValueError(f"Duplicate model reference: {manifest.ref}")
            self._models[manifest.ref] = manifest

    def publisher_url(self, ref):
        model = self.manifest(ref)
        return model.publisher_url if model else None

    def manifest(self, ref) -> ModelManifest | None:
        return self._models.get(ref)

    def check(self, ref, root: Path, backend: str):
        if ref is None:
            return ()
        model = self._models.get(ref)
        if model is None:
            return (AssetIssue("MODEL_UNKNOWN", f"No reviewed local manifest for {ref}; register its identity and rights evidence"),)
        issues = []
        if backend not in model.backends:
            issues.append(AssetIssue("MODEL_BACKEND_UNSUPPORTED", f"{ref} {model.version} does not support prepared backend {backend}"))
        for item in model.files:
            path = root / Path(item.relative_path.replace("\\", "/"))
            try:
                resolved_root, resolved = root.resolve(strict=True), path.resolve(strict=True)
                if not resolved.is_relative_to(resolved_root) or not resolved.is_file():
                    issues.append(AssetIssue("MODEL_PATH_INVALID", f"Expected a model file inside {resolved_root}: {path}", path))
                    continue
                with resolved.open("rb") as stream:
                    actual = hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != item.sha256:
                    issues.append(AssetIssue("MODEL_CHECKSUM_INVALID", f"{ref} {model.version}: checksum mismatch; supply the exact reviewed file: {path}", path))
            except FileNotFoundError:
                issues.append(AssetIssue("MODEL_MISSING", f"{ref} {model.version}: supply required file {path}", path))
            except (OSError, RuntimeError) as exc:
                # The diagnostic cause is retained in the actionable issue text.
                issues.append(AssetIssue("MODEL_UNREADABLE", f"Cannot validate {path}: {type(exc).__name__}: {exc}", path, traceback.format_exc()))
        return tuple(issues)
