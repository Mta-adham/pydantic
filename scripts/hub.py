"""Load benchmark definitions and verify immutable Docker image digests."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
DEFAULT_TIMEOUT_S = 1800


def hub_root() -> Path:
    return Path(os.environ.get("GSO_WORKSPACE_ROOT", ".")).resolve()


def eval_dir(root: Path | None = None) -> Path:
    return (root or hub_root()) / "eval"


def benchmark_slug(instance_id: str) -> str:
    if "__" in instance_id:
        return instance_id.split("__", 1)[-1]
    return instance_id


def eval_task_dir(root: Path, instance_id: str) -> Path | None:
    """Find the eval task directory for instance_id by scanning eval/*/benchmark.yaml."""
    base = eval_dir(root)
    if not base.is_dir():
        return None
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        path = entry / "benchmark.yaml"
        if not path.is_file():
            continue
        try:
            data = yaml.safe_load(path.read_text())
        except Exception:
            continue
        if isinstance(data, dict) and data.get("instance_id") == instance_id:
            return entry
    return None


def optimization_guide_path(root: Path, instance_id: str) -> Path | None:
    task_dir = eval_task_dir(root, instance_id)
    if task_dir is None:
        return None
    p = task_dir / "OPTIMIZATION.md"
    return p if p.is_file() else None


def gso_task_id_path(root: Path | None = None) -> Path:
    return (root or hub_root()) / ".gso_task_id"


def read_gso_task_instance_id(root: Path | None = None) -> str | None:
    path = gso_task_id_path(root)
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text())
    if isinstance(data, dict) and data.get("instance_id"):
        return str(data["instance_id"])
    return None


def sync_gso_task_id(root: Path, instance_id: str) -> Path:
    """Write .gso_task_id from eval/<task>/benchmark.yaml."""
    defn = load_benchmark_def(root, instance_id)
    path = gso_task_id_path(root)
    path.write_text(yaml.safe_dump(defn, sort_keys=False, default_flow_style=False))
    return path


def list_instance_ids(root: Path | None = None) -> list[str]:
    """All instance IDs from eval/*/benchmark.yaml (sorted)."""
    root = root or hub_root()
    ids: list[str] = []
    base = eval_dir(root)
    if not base.is_dir():
        return ids
    for entry in sorted(base.iterdir()):
        if not entry.is_dir():
            continue
        path = entry / "benchmark.yaml"
        if not path.is_file():
            continue
        data = yaml.safe_load(path.read_text())
        if isinstance(data, dict) and data.get("instance_id"):
            ids.append(str(data["instance_id"]))
    return ids


def load_benchmark_def(root: Path, instance_id: str) -> dict[str, Any]:
    task_dir = eval_task_dir(root, instance_id)
    if task_dir is None:
        task_path = gso_task_id_path(root)
        if task_path.is_file():
            data = yaml.safe_load(task_path.read_text())
            if isinstance(data, dict) and data.get("instance_id") == instance_id:
                return data
        raise SystemExit(
            f"No benchmark definition for {instance_id}\n"
            "Each task must have eval/<task>/benchmark.yaml with a pinned digest."
        )
    path = task_dir / "benchmark.yaml"
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise SystemExit(f"Invalid benchmark definition (expected mapping): {path}")
    if data.get("instance_id") != instance_id:
        raise SystemExit(
            f"benchmark.yaml instance_id mismatch: "
            f"expected {instance_id}, got {data.get('instance_id')} in {path}"
        )
    return data


def timeout_for_instance(root: Path, instance_id: str) -> int:
    defn = load_benchmark_def(root, instance_id)
    runner = defn.get("runner") or {}
    return int(runner.get("timeout_seconds", DEFAULT_TIMEOUT_S))


def definition_sha256(defn: dict[str, Any]) -> str:
    canonical = json.dumps(defn, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def normalize_digest(digest: str) -> str:
    digest = digest.strip()
    if digest.startswith("sha256:"):
        return digest
    if re.fullmatch(r"[a-f0-9]{64}", digest):
        return f"sha256:{digest}"
    raise SystemExit(f"Invalid digest format (expected sha256:...): {digest!r}")


def require_digest(defn: dict[str, Any]) -> str:
    target = defn.get("target") or {}
    digest = target.get("digest")
    if not digest:
        image = target.get("image", "<unknown>")
        raise SystemExit(
            f"Benchmark {defn.get('name')} has no pinned image digest.\n"
            f"Run: ./scripts/images.sh pin-images {defn.get('instance_id')}\n"
            f"Refusing to evaluate {image} without an immutable digest."
        )
    digest = normalize_digest(str(digest))
    if not DIGEST_RE.match(digest):
        raise SystemExit(f"Invalid digest in benchmark definition: {digest!r}")
    return digest


def local_image_digest(image_tag: str) -> str | None:
    proc = subprocess.run(
        ["docker", "image", "inspect", image_tag, "--format", "{{json .RepoDigests}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    try:
        digests = json.loads(proc.stdout.strip() or "[]")
    except json.JSONDecodeError:
        return None
    for entry in digests:
        if "@sha256:" in entry:
            return entry.split("@", 1)[1]
    proc2 = subprocess.run(
        ["docker", "image", "inspect", image_tag, "--format", "{{.Id}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc2.returncode == 0 and proc2.stdout.strip().startswith("sha256:"):
        return proc2.stdout.strip()
    return None


def image_matches_pinned_digest(defn: dict[str, Any]) -> bool:
    try:
        expected = require_digest(defn)
    except SystemExit:
        return False
    image_tag = (defn.get("target") or {}).get("image")
    if not image_tag:
        return False
    return local_image_digest(image_tag) == expected


def _verification_record(
    defn: dict[str, Any],
    instance_id: str,
    *,
    resolved_digest: str,
    skipped: bool = False,
) -> dict[str, Any]:
    target = defn["target"]
    record = {
        "benchmark": defn.get("name"),
        "instance_id": instance_id,
        "benchmark_def_sha256": definition_sha256(defn),
        "dataset_version": defn.get("dataset_version"),
        "target": {
            "image": target["image"],
            "digest": require_digest(defn),
            "resolved_digest": resolved_digest,
        },
        "git": defn.get("git") or {},
    }
    if skipped:
        record["skipped"] = True
    return record


def pull_image_by_digest(defn: dict[str, Any]) -> str:
    target = defn.get("target") or {}
    image_tag = target["image"]
    expected = require_digest(defn)
    ref = f"{image_tag}@{expected}"

    print(f"Pulling immutable image: {ref}")
    subprocess.run(["docker", "pull", ref], check=True)

    resolved = local_image_digest(image_tag)
    if not resolved:
        subprocess.run(["docker", "tag", ref, image_tag], check=True)
        resolved = local_image_digest(image_tag) or expected

    if resolved != expected:
        raise SystemExit(
            f"Image digest mismatch for {defn.get('name')}:\n"
            f"  benchmark.yaml: {expected}\n"
            f"  resolved:       {resolved}\n"
            "Refusing to run — update benchmark.yaml via ./scripts/images.sh pin-images if the image was rebuilt."
        )

    print(f"Verified image digest: {resolved}")
    return resolved


def pull_benchmark_image(root: Path, instance_id: str) -> dict[str, Any] | None:
    defn = load_benchmark_def(root, instance_id)
    image_tag = defn["target"]["image"]
    expected = require_digest(defn)

    if image_matches_pinned_digest(defn):
        print(f"Skipping pull (already present): {image_tag} @ {expected}")
        return None

    resolved = pull_image_by_digest(defn)
    return _verification_record(defn, instance_id, resolved_digest=resolved)


def verify_benchmark_image(
    root: Path,
    instance_id: str,
    *,
    pull: bool = True,
    skip_if_present: bool = False,
) -> dict[str, Any]:
    defn = load_benchmark_def(root, instance_id)
    expected = require_digest(defn)
    image_tag = defn["target"]["image"]

    if skip_if_present and image_matches_pinned_digest(defn):
        print(f"Skipping verify (already present): {image_tag} @ {expected}")
        return _verification_record(
            defn, instance_id, resolved_digest=expected, skipped=True
        )

    resolved = local_image_digest(image_tag)
    if resolved != expected:
        if not pull:
            raise SystemExit(
                f"Local image {image_tag} digest {resolved!r} != pinned {expected}.\n"
                "Run ./scripts/images.sh pull-images to fetch the pinned digest."
            )
        resolved = pull_image_by_digest(defn)
    else:
        print(f"Image digest OK: {image_tag} @ {resolved}")

    return _verification_record(defn, instance_id, resolved_digest=resolved)


def pin_digest_from_registry(root: Path, instance_id: str) -> str:
    task_dir = eval_task_dir(root, instance_id)
    if task_dir is None:
        raise SystemExit(f"No eval task directory found for {instance_id}")
    path = task_dir / "benchmark.yaml"
    defn = load_benchmark_def(root, instance_id)
    image_tag = defn["target"]["image"]

    print(f"Pulling {image_tag} to resolve digest...")
    subprocess.run(["docker", "pull", image_tag], check=True)

    digest = local_image_digest(image_tag)
    if not digest:
        raise SystemExit(f"Could not resolve digest for {image_tag} after pull.")

    digest = normalize_digest(digest)
    defn["target"]["digest"] = digest
    path.write_text(yaml.safe_dump(defn, sort_keys=False, default_flow_style=False))
    print(f"Pinned {instance_id}: {digest}")
    print(f"Updated {path}")
    return digest


def provenance_block(
    root: Path,
    instance_id: str,
    *,
    gso_version: str,
    recorded_at: str,
    verified: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defn = load_benchmark_def(root, instance_id)
    git = defn.get("git") or {}
    base_commit = git.get("base_commit", "")
    git_sha = base_commit.rstrip("^")[:12] if base_commit else None
    target = (verified or {}).get("target") or defn.get("target") or {}

    return {
        "benchmark": defn.get("name"),
        "instance_id": instance_id,
        "timestamp": recorded_at,
        "benchmark_def_sha256": definition_sha256(defn),
        "dataset_version": defn.get("dataset_version"),
        "image": target.get("image"),
        "image_digest": target.get("resolved_digest") or target.get("digest"),
        "git_repo": git.get("repo"),
        "git_sha": git_sha,
        "git_base_commit": base_commit,
        "gso_version": gso_version,
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    root = hub_root()

    if not argv or argv[0] in {"-h", "--help"}:
        print("Usage: hub.py list | timeout <id> | pull <id> | verify <id> | pin <id>")
        return 0

    cmd = argv[0]
    if cmd == "list":
        for instance_id in list_instance_ids(root):
            defn = load_benchmark_def(root, instance_id)
            timeout = (defn.get("runner") or {}).get("timeout_seconds", "")
            print(f"{instance_id}\t{timeout}")
        return 0

    if len(argv) < 2:
        raise SystemExit(f"instance_id required for: {cmd}")

    instance_id = argv[1]

    if cmd == "timeout":
        print(timeout_for_instance(root, instance_id))
        return 0
    if cmd == "pull":
        pull_benchmark_image(root, instance_id)
        return 0
    if cmd == "verify":
        verify_benchmark_image(
            root,
            instance_id,
            pull="--pull" in argv,
            skip_if_present="--force" not in argv,
        )
        return 0
    if cmd == "pin":
        pin_digest_from_registry(root, instance_id)
        return 0

    raise SystemExit(f"Unknown command: {cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
