#!/usr/bin/env python3
"""Local workflow: edit project files, diff against frozen baseline/, build patch, evaluate."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from gso.utils.io import load_gso_dataset

def hub_root() -> Path:
    if env := os.environ.get("GSO_WORKSPACE_ROOT"):
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def gso_log_dir() -> Path:
    """Harness logs under the hub tree (not outside this repo)."""
    return hub_root() / "logs" / "run_evaluation"


DOCKER_NAMESPACE = "slimshetty/gso"
ARTEMIS_BENCHMARK_FILENAME = "artemis_results.json"
ARTEMIS_BENCHMARK_ROBUST_FILENAME = "artemis_results_robust.json"
ARTEMIS_TEST_FILENAME = "tests_artemis_results.json"
_VERIFIED_PROVENANCE: dict[str, dict[str, Any]] = {}


def load_instance(instance_id: str):
    return _load_hub_instance(benchmark_root(), instance_id)


def _load_hub_instance(root: Path, instance_id: str):
    """Load a task instance for the benchmark hub (cached JSON or HuggingFace)."""
    runner = _runner_module()
    task_dir = runner.eval_task_dir(root, instance_id)
    cache = task_dir / "instance.json" if task_dir else None
    if cache and cache.is_file():
        from gso.data.dataset import GSOInstance

        return GSOInstance(**json.loads(cache.read_text()))

    defn = runner.load_benchmark_def(root, instance_id)
    dataset_version = str(defn.get("dataset_version") or "gso-bench/gso@test")
    name, _, split = dataset_version.partition("@")
    if not split:
        name, split = dataset_version, "test"
    matches = load_gso_dataset(name=name, split=split, instance_ids=[instance_id])
    if not matches:
        raise SystemExit(
            f"Unknown instance_id: {instance_id}\n"
            f"Check dataset_version in eval/<task>/benchmark.yaml "
            f"and HF_TOKEN for HuggingFace access."
        )
    instance = matches[0]
    if cache:
        try:
            cache.write_text(json.dumps(instance.__dict__, indent=2) + "\n")
        except OSError:
            pass
    return instance


def changed_lines_per_file(diff_text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    current: str | None = None
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            match = re.match(r"diff --git a/(.*?) b/", line)
            current = match.group(1) if match else None
            if current:
                counts.setdefault(current, 0)
        elif current and line[:1] in {"+", "-"} and not line.startswith(("+++", "---")):
            counts[current] += 1
    return counts


def is_incidental_path(rel_path: str) -> bool:
    """Paths touched in expert commits but not meaningful optimization targets."""
    name = Path(rel_path).name
    if name in {"pdm.lock", "uv.lock", "poetry.lock", "package-lock.json", ".gitignore"}:
        return True
    if rel_path.startswith(("docs/", "changes/")):
        return True
    return False


def patch_file_list(meta: dict) -> list[str]:
    """Source files to diff for a task (drops lockfiles and docs churn)."""
    return [p for p in meta.get("files", []) if not is_incidental_path(p)]


def files_from_diff(
    diff_text: str,
    *,
    include_tests: bool = False,
    min_changed_lines: int = 10,
) -> list[str]:
    counts = changed_lines_per_file(diff_text)
    paths = re.findall(r"^diff --git a/(.*?) b/", diff_text, re.MULTILINE)
    seen: set[str] = set()
    candidates: list[str] = []
    for path in paths:
        if path in seen:
            continue
        if not include_tests and is_test_path(path):
            continue
        if is_incidental_path(path):
            continue
        seen.add(path)
        candidates.append(path)

    substantial = [p for p in candidates if counts.get(p, 0) >= min_changed_lines]
    if substantial:
        return substantial

    if not candidates:
        return []

    # If only minor edits exist (e.g. incidental .strip() tweaks), keep the
    # file with the largest diff so the workspace still has a clear target.
    primary = max(candidates, key=lambda p: counts.get(p, 0))
    return [primary]


def is_test_path(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if parts and parts[0] in {"tests", "test", "testing"}:
        return True
    name = Path(rel_path).name
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith("_tests.py")
    )


def project_root() -> Path:
    if env := os.environ.get("GSO_PROJECT_ROOT"):
        return Path(env).expanduser().resolve()
    raise SystemExit("GSO_PROJECT_ROOT is not set.")


def benchmark_root() -> Path:
    return project_root().parent


def _runner_module():
    """Load scripts/hub.py for benchmark definitions."""
    hub_py = benchmark_root() / "scripts" / "hub.py"
    if not hub_py.is_file():
        raise SystemExit(f"Missing hub module: {hub_py}")
    spec = importlib.util.spec_from_file_location(
        "pydantic_benchmark_hub", hub_py
    )
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load hub module: {hub_py}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def gso_version() -> str:
    try:
        from importlib.metadata import version

        return version("gsobench")
    except Exception:
        return "unknown"


def verify_instance_image(instance_id: str, *, pull: bool = True) -> dict[str, Any]:
    """Require pinned digest from benchmarks/*/benchmark.yaml before harness runs."""
    verified = _runner_module().verify_benchmark_image(
        benchmark_root(), instance_id, pull=pull
    )
    _VERIFIED_PROVENANCE[instance_id] = verified
    return verified


def build_provenance(instance_id: str, recorded_at: str) -> dict[str, Any] | None:
    verified = _VERIFIED_PROVENANCE.get(instance_id)
    return _runner_module().provenance_block(
        benchmark_root(),
        instance_id,
        gso_version=gso_version(),
        recorded_at=recorded_at,
        verified=verified,
    )


def eval_dir_slug(instance_id: str, base_commit: str | None = None) -> str:
    """eval-<task>-<commit> — one folder per task/image (1:1)."""
    if base_commit is None:
        base_commit = load_instance(instance_id).base_commit
    short_commit = base_commit.rstrip("^")[:7]
    task_part = instance_id.split("__", 1)[-1] if "__" in instance_id else instance_id
    return f"eval-{task_part}-{short_commit}"


def workspace_dir(instance_id: str) -> Path:
    instance = load_instance(instance_id)
    slug = eval_dir_slug(instance_id, instance.base_commit)
    return benchmark_root() / "eval" / slug


def read_active_instance_id() -> str | None:
    """Active task from .gso_task_id."""
    return _runner_module().read_gso_task_instance_id(benchmark_root())


def set_active_task(instance_id: str) -> None:
    _runner_module().sync_gso_task_id(benchmark_root(), instance_id)


def is_git_work_tree(path: Path) -> bool:
    """True if path is the root of its own git checkout (not just inside a parent repo)."""
    if not path.is_dir():
        return False
    proc = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False
    return Path(proc.stdout.strip()).resolve() == path.resolve()


def project_commit_matches_task(instance_id: str) -> bool:
    proj = project_root()
    if not is_git_work_tree(proj):
        return False
    instance = load_instance(instance_id)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=proj,
        capture_output=True,
        text=True,
        check=False,
    )
    expected = subprocess.run(
        ["git", "rev-parse", instance.base_commit],
        cwd=proj,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0 or expected.returncode != 0:
        return False
    return head.stdout.strip() == expected.stdout.strip()


def activate_task_for_editing(instance_id: str, *, checkout: bool = True) -> None:
    """Mark a task active and optionally sync project/ to its base commit."""
    set_active_task(instance_id)
    if not checkout:
        return
    proj = project_root()
    instance = load_instance(instance_id)
    if not is_git_work_tree(proj):
        ensure_project_git_repo(instance, proj)
    else:
        checkout_project_at_commit(instance, proj)


def _task_switch_allowed() -> bool:
    return os.environ.get("GSO_ALLOW_TASK_SWITCH", "").strip() in {
        "1",
        "true",
        "yes",
    }


def prepare_command_hint(instance_id: str | None = None) -> str:
    """CLI hint for creating or switching a prepared workspace."""
    if instance_id:
        return f"./compile {instance_id}"
    return "./compile <task_id>"


def harness_command_hint(instance_id: str, *, command: str = "benchmark") -> str:
    return f"./{command} {instance_id}"


def require_active_task(
    instance_id: str,
    *,
    action: str,
    checkout_on_switch: bool = False,
) -> None:
    """Refuse compile/benchmark/test when instance_id != prepared active task."""
    active = read_active_instance_id()
    if active == instance_id:
        return

    if active and _task_switch_allowed():
        print(
            f"Switching active task: {active or '<none>'} -> {instance_id} "
            f"({action})"
        )
        activate_task_for_editing(instance_id, checkout=checkout_on_switch)
        return

    if not active:
        raise SystemExit(
            f"No active task for {action}.\n"
            f"Run: {prepare_command_hint(instance_id)}"
        )

    runner = _runner_module()
    image_hint = ""
    try:
        defn = runner.load_benchmark_def(benchmark_root(), instance_id)
        digest = (defn.get("target") or {}).get("digest", "")
        image_hint = (
            f"\n  requested image: {(defn.get('target') or {}).get('image')}@{digest}"
        )
    except SystemExit:
        pass

    raise SystemExit(
        f"Task mismatch: cannot {action} {instance_id} while "
        f"{active} is the active task.\n"
        f"  .gso_task_id: {benchmark_root() / '.gso_task_id'}\n"
        f"  project/ is checked out for {active}, not {instance_id}.{image_hint}\n"
        f"Switch tasks: {prepare_command_hint(instance_id)}\n"
        f"Or continue:   ./{action.split()[0]} {active}"
    )


def require_project_matches_active_task(instance_id: str) -> None:
    """Refuse compile when project/ HEAD != task base commit."""
    if project_commit_matches_task(instance_id):
        return
    raise SystemExit(
        f"project/ is not checked out to {instance_id}'s base commit.\n"
        f"Run: {prepare_command_hint(instance_id)}"
    )


def format_active_task_status() -> str:
    active = read_active_instance_id()
    if not active:
        return f"Active task: <none> (run {prepare_command_hint()})"
    parts = [f"Active task: {active}", f".gso_task_id: {benchmark_root() / '.gso_task_id'}"]
    runner = _runner_module()
    try:
        defn = runner.load_benchmark_def(benchmark_root(), active)
        target = defn.get("target") or {}
        digest = target.get("digest", "")
        parts.append(f"image: {target.get('image')}@{digest}")
    except SystemExit:
        pass
    if project_commit_matches_task(active):
        parts.append("project/: commit OK")
    else:
        parts.append(f"project/: commit MISMATCH (run {prepare_command_hint(active)})")
    return "\n".join(parts)


def print_benchmark_hub_edit_hints(instance_id: str, paths: list[str]) -> None:
    """Instructions for editing project/ in the pydantic benchmark hub."""
    proj = project_root()
    print(format_active_task_status())
    print("Edit the pydantic project (evaluated by GSO harness in Docker):")
    print(f"  project: {proj}")
    print(f"  gso:     slimshetty/gso:gso.eval.x86_64.{instance_id.lower()}")
    for path in paths:
        print(f"  edit:    project/{path}")


def metadata_path(instance_id: str) -> Path:
    return workspace_dir(instance_id) / "metadata.json"


def load_metadata(instance_id: str) -> dict:
    path = metadata_path(instance_id)
    if not path.exists():
        raise SystemExit(
            f"No eval workspace for {instance_id}. "
            f"Run: {prepare_command_hint(instance_id)}"
        )
    return json.loads(path.read_text())


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        raise SystemExit(
            f"Command failed ({result.returncode}): {' '.join(cmd)}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout + result.stderr


def ensure_project_git_repo(instance, project_dir: Path) -> None:
    """Replace vendored project/ with a git clone so task commits can be checked out."""
    if is_git_work_tree(project_dir):
        return
    if not project_dir.is_dir() or not any(project_dir.iterdir()):
        project_dir.mkdir(parents=True, exist_ok=True)
        print(f"Cloning {instance.repo} into {project_dir}...")
        clone_repo(instance, project_dir)
        return
    print(
        f"Replacing vendored {project_dir.name}/ with git clone "
        f"(source files remain in the hub repo)..."
    )
    clone_repo(instance, project_dir)


def clone_repo(instance, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            f"https://github.com/{instance.repo}.git",
            str(dest),
        ]
    )
    run(["git", "checkout", instance.base_commit], cwd=dest)


def _checkout_git_commit(
    project_dir: Path, commit: str, *, label: str | None = None
) -> None:
    commit = commit.strip()
    if label:
        print(f"Checking out {label} @ {commit[:12]}...")

    def try_checkout() -> bool:
        proc = subprocess.run(
            ["git", "checkout", "--force", commit],
            cwd=project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0

    if try_checkout():
        return

    base_ref = commit.rstrip("^")
    depth = "2" if commit.endswith("^") else "1"
    print(f"Commit not in local clone; fetching origin {base_ref[:12]} (depth {depth})...")
    run(
        ["git", "fetch", "--depth", depth, "origin", base_ref],
        cwd=project_dir,
    )
    if not try_checkout():
        raise SystemExit(
            f"Could not checkout {commit} in {project_dir}.\n"
            f"Try: git fetch --unshallow && {prepare_command_hint()}"
        )


def checkout_project_at_commit(instance, project_dir: Path) -> None:
    if not is_git_work_tree(project_dir):
        raise SystemExit(
            f"Not a git repository: {project_dir}\n"
            f"Run {prepare_command_hint()} to initialize it."
        )
    _checkout_git_commit(project_dir, instance.base_commit, label=instance.repo)


def populate_expert_dir(instance, root: Path, rel_files: list[str]) -> None:
    """Copy task files at opt_commit into eval/<task>/expert/ for local reference."""
    expert_dir = root / "expert"
    if expert_dir.is_dir() and any(expert_dir.rglob("*")):
        sync_optimization_guide(instance.instance_id, expert_dir)
        return
    expert_src = root / ".expert_src"
    try:
        if expert_src.exists():
            shutil.rmtree(expert_src)
        print(f"Materializing expert/ @ {instance.opt_commit[:12]}...")
        clone_repo(instance, expert_src)
        _checkout_git_commit(expert_src, instance.opt_commit)
        for rel_path in rel_files:
            src = expert_src / rel_path
            if not src.is_file():
                print(f"Warning: expert file missing: {rel_path}")
                continue
            dst = expert_dir / rel_path
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        if any(expert_dir.rglob("*")):
            print(f"Wrote expert/: {expert_dir}")
        sync_optimization_guide(instance.instance_id, expert_dir)
    finally:
        shutil.rmtree(expert_src, ignore_errors=True)


def sync_optimization_guide(instance_id: str, expert_dir: Path) -> None:
    """Copy eval/<task>/OPTIMIZATION.md into eval/<task>/expert/ when present."""
    src = _runner_module().optimization_guide_path(benchmark_root(), instance_id)
    if not src:
        return
    expert_dir.mkdir(parents=True, exist_ok=True)
    dst = expert_dir / "OPTIMIZATION.md"
    shutil.copy2(src, dst)
    print(f"Wrote optimization guide: {dst}")


def setup_workspace(
    instance_id: str,
    files: list[str] | None = None,
    force: bool = False,
    include_tests: bool = False,
) -> Path:
    instance = load_instance(instance_id)
    root = workspace_dir(instance_id)
    proj = project_root()

    if root.exists() and not force:
        activate_task_for_editing(instance_id, checkout=True)
        meta_path = metadata_path(instance_id)
        if meta_path.exists():
            meta_paths = json.loads(meta_path.read_text()).get("files", [])
            populate_expert_dir(instance, root, meta_paths)
        else:
            meta_paths = []
        if os.environ.get("GSO_QUIET_PREPARE", "").strip() == "1":
            print(f"Ready: {instance_id} — project/ synced, eval/{root.name}")
            return root
        print(f"Workspace already exists: {root}")
        print_benchmark_hub_edit_hints(instance_id, meta_paths)
        return root

    if root.exists():
        shutil.rmtree(root)

    baseline_dir = root / "baseline"
    baseline_dir.mkdir(parents=True)

    if not is_git_work_tree(proj):
        ensure_project_git_repo(instance, proj)
    else:
        checkout_project_at_commit(instance, proj)
    source_dir = proj

    rel_files = files or files_from_diff(instance.gt_diff, include_tests=include_tests)
    if not rel_files:
        raise SystemExit(
            "Could not determine files to extract. Pass --files path/to/file.py"
        )

    all_paths = re.findall(
        r"^diff --git a/(.*?) b/", instance.gt_diff, re.MULTILINE
    )
    skipped_tests = (
        [p for p in all_paths if is_test_path(p)] if not include_tests else []
    )
    skipped_minor = [
        p for p in all_paths if not is_test_path(p) and p not in rel_files
    ]
    if skipped_tests and not include_tests:
        print("Skipping test files (edit source only; tests are run by the harness):")
        for path in skipped_tests:
            print(f"  - {path}")
    if skipped_minor:
        print("Skipping minor/incidental source files (small expert-commit tweaks):")
        for path in skipped_minor:
            print(f"  - {path}")

    copied: list[str] = []
    for rel_path in rel_files:
        src = source_dir / rel_path
        if not src.exists():
            print(f"Warning: skipping missing file: {rel_path}")
            continue
        baseline_dst = baseline_dir / rel_path
        baseline_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, baseline_dst)
        copied.append(rel_path)

    if not copied:
        raise SystemExit("No files were copied into baseline/.")

    populate_expert_dir(instance, root, copied)

    meta = {
        "instance_id": instance.instance_id,
        "repo": instance.repo,
        "base_commit": instance.base_commit,
        "api": instance.api,
        "files": copied,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    metadata_path(instance_id).write_text(json.dumps(meta, indent=2))

    print(f"Workspace ready: {root}")
    set_active_task(instance_id)
    print_benchmark_hub_edit_hints(instance_id, copied)
    return root


def reset_workspace_edits(instance_id: str) -> Path:
    """Restore editable files from baseline/ (discard edits in project/)."""
    meta = load_metadata(instance_id)
    root = workspace_dir(instance_id)
    baseline_dir = root / "baseline"
    work_dir = project_root()
    if not baseline_dir.is_dir():
        raise SystemExit(f"Missing baseline/ for {instance_id}")
    if not work_dir.is_dir():
        raise SystemExit(f"Missing project/ for {instance_id}")

    restored: list[str] = []
    for rel_path in patch_file_list(meta) or meta.get("files", []):
        src = baseline_dir / rel_path
        dst = work_dir / rel_path
        if not src.exists():
            print(f"Warning: missing baseline file: {rel_path}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        restored.append(rel_path)

    if not restored:
        raise SystemExit(f"No files restored for {instance_id}")

    print(f"Restored {len(restored)} file(s) from baseline/ → project/")
    for path in restored:
        print(f"  - {path}")
    return root


GSO_PLACEHOLDER_MARKER = "gso-placeholder"
GSO_PLACEHOLDER_MARKERS = (GSO_PLACEHOLDER_MARKER, "gso-noop")


def placeholder_marker_for_file(rel_path: str) -> str:
    """Return a comment line that is valid for the file type (patch must compile)."""
    suffix = Path(rel_path).suffix.lower()
    if suffix in {".py", ".pyx", ".pxi", ".sh", ".yaml", ".yml", ".toml"}:
        return f"# {GSO_PLACEHOLDER_MARKER}\n"
    return f"// {GSO_PLACEHOLDER_MARKER}\n"


def build_placeholder_patch(baseline_dir: Path, rel_path: str) -> str:
    """Build patch with only an automatic tooling marker when there are zero edits."""
    import tempfile

    orig = baseline_dir / rel_path
    content = orig.read_text()
    marker = placeholder_marker_for_file(rel_path)
    with tempfile.NamedTemporaryFile("w", suffix=Path(rel_path).suffix, delete=False) as tmp:
        tmp.write(content)
        if not content.endswith("\n"):
            tmp.write("\n")
        tmp.write(marker)
        tmp_path = tmp.name
    proc = subprocess.run(
        [
            "diff",
            "-u",
            "--label",
            f"a/{rel_path}",
            "--label",
            f"b/{rel_path}",
            str(orig),
            tmp_path,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    Path(tmp_path).unlink(missing_ok=True)
    if proc.returncode != 1 or not proc.stdout.strip():
        raise SystemExit("Failed to build placeholder patch for unchanged workspace.")
    return proc.stdout


def build_patch(
    instance_id: str,
    model_name: str = "local-edit",
    *,
    placeholder_on_unchanged: bool = False,
) -> tuple[str, Path] | None:
    require_active_task(instance_id, action="compile", checkout_on_switch=True)
    require_project_matches_active_task(instance_id)
    meta = load_metadata(instance_id)
    root = workspace_dir(instance_id)
    baseline_dir = root / "baseline"
    work_dir = project_root()
    rel_files = patch_file_list(meta)
    if not rel_files:
        rel_files = list(meta.get("files", []))

    chunks: list[str] = []
    for rel_path in rel_files:
        orig = baseline_dir / rel_path
        edited = work_dir / rel_path
        if not orig.exists():
            continue
        if not edited.exists():
            raise SystemExit(
                f"Missing edited file: {edited}\n"
                f"Run: {prepare_command_hint(instance_id)}"
            )

        proc = subprocess.run(
            [
                "diff",
                "-u",
                "--label",
                f"a/{rel_path}",
                "--label",
                f"b/{rel_path}",
                str(orig),
                str(edited),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode == 1:
            chunks.append(proc.stdout)
        elif proc.returncode != 0:
            raise SystemExit(proc.stderr or "diff failed")

    patch = "".join(chunks)
    edit_label = "project"
    if not patch.strip():
        if placeholder_on_unchanged:
            rel_path = rel_files[0]
            patch = build_placeholder_patch(baseline_dir, rel_path)
            print(
                f"No code changes in {edit_label}/ for {instance_id}; "
                "using automatic placeholder marker so the harness can run."
            )
        else:
            raise SystemExit(
                f"No changes found between baseline/ and {edit_label}/. "
                f"Edit {edit_label}/ first or pass --placeholder-on-unchanged."
            )

    patch_path = root / "patch.diff"
    patch_path.write_text(patch)

    prediction = {
        "instance_id": instance_id,
        "model_patch": patch,
        "model_name_or_path": model_name,
    }
    predictions_path = root / "predictions.jsonl"
    predictions_path.write_text(json.dumps(prediction) + "\n")

    print(f"Wrote patch: {patch_path}")
    print(f"Wrote predictions: {predictions_path}")
    return patch, predictions_path


def docker_image_name(instance) -> str:
    return (
        f"{DOCKER_NAMESPACE}:gso.eval.{instance.arch}."
        f"{instance.instance_id.lower()}"
    )


def cleanup_stale_harness_containers(instance_id: str) -> None:
    """Remove leftover GSO eval containers that block re-runs for this task."""
    proc = subprocess.run(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            f"name=gso.eval.{instance_id}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    for cid in proc.stdout.splitlines():
        cid = cid.strip()
        if not cid:
            continue
        rm = subprocess.run(
            ["docker", "rm", "-f", cid],
            capture_output=True,
            text=True,
            check=False,
        )
        if rm.returncode == 0:
            print(f"Removed stale harness container: {cid[:12]}")


def remove_docker_image(image: str) -> None:
    proc = subprocess.run(
        ["docker", "rmi", "-f", image],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        print(f"Removed docker image: {image}")
    elif "No such image" not in (proc.stderr or ""):
        print(f"Warning: could not remove {image}: {proc.stderr.strip()}")


def cleanup_instance_images(instance) -> None:
    """Remove local and remote tags for a task image."""
    for image in {
        docker_image_name(instance),
        instance.remote_instance_image_key,
        instance.instance_image_key,
    }:
        remove_docker_image(image)


def predictions_path(instance_id: str) -> Path:
    return workspace_dir(instance_id) / "predictions.jsonl"


def hub_artemis_benchmark_path() -> Path:
    return hub_root() / ARTEMIS_BENCHMARK_FILENAME


def hub_artemis_benchmark_robust_path() -> Path:
    return hub_root() / ARTEMIS_BENCHMARK_ROBUST_FILENAME


def hub_artemis_test_path() -> Path:
    return hub_root() / ARTEMIS_TEST_FILENAME


def hub_summary_path() -> Path:
    return hub_root() / "summary.txt"


def _format_baseline_opt_line(
    passed: bool | None, speedup: float | None
) -> str:
    need = BASELINE_OPT_PERCENT
    pct = _percent_faster(speedup)
    if pct is None:
        if passed is None:
            return "n/a"
        return (
            f"yes (≥{need}% faster required)"
            if passed
            else f"no (≥{need}% faster required)"
        )
    if pct >= 0:
        actual = f"{pct:.2f}% faster than baseline"
    else:
        actual = f"{abs(pct):.2f}% slower than baseline"
    if passed:
        return f"yes — {actual} (≥{need}% required)"
    return f"no — {actual} (needs ≥{need}%)"


def _format_expert_opt_line(
    passed: bool | None, parity_percent: float | None
) -> str:
    need = EXPERT_MATCH_PERCENT
    if parity_percent is None:
        if passed is None:
            return "n/a"
        return (
            f"yes (≥{need}% of expert speed required)"
            if passed
            else f"no (≥{need}% of expert speed required)"
        )
    if passed:
        return f"yes — {parity_percent:.1f}% of expert speed (≥{need}% required)"
    return f"no — {parity_percent:.1f}% of expert speed (needs ≥{need}%)"


def write_comparison_summary(
    instance_id: str, run_id: str, model_name: str = "local-edit"
) -> Path:
    """Human-readable baseline vs project summary at hub root summary.txt."""
    instance_report = load_instance_report(instance_id, run_id, model_name)
    parts = build_improvement_summary(instance_report, instance_id=instance_id)
    summary = parts["summary"]
    runtime = summary.get("runtime_s") or {}
    vs_base = summary.get("vs_baseline") or {}
    vs_expert = summary.get("vs_expert") or {}
    expert_vs_baseline = summary.get("expert_vs_baseline") or {}
    eval_metrics = summary.get("eval") or {}
    harness = summary.get("harness") or {}
    confidence = summary.get("confidence") or {}
    measurement = summary.get("measurement") or {}

    def _fmt_seconds(value: float | None) -> str:
        if value is None:
            return "n/a"
        return f"{value:.6f}s"

    def _harness_status(value: bool | None, *, yes: str, no: str) -> str:
        if value is None:
            return "n/a"
        return yes if value else no

    tests_passed = harness.get("tests_passed")
    tests_total = harness.get("tests_total")
    if tests_passed is None and instance_report.get("test_passed") is not None:
        tests_total = tests_total or len(
            (instance_report.get("time_stats") or {})
            .get("per_test_means", {})
            .get("base", [])
            or instance_report.get("base_times")
            or []
        )
        tests_passed = (
            tests_total if instance_report.get("test_passed") else 0
        )
    run_ok = (
        tests_total is not None
        and tests_passed is not None
        and tests_total > 0
        and tests_passed == tests_total
    )

    provenance = build_provenance(
        instance_id, datetime.now(timezone.utc).isoformat()
    )
    lines = [
        f"instance_id: {instance_id}",
        f"run_id: {run_id}",
    ]
    if provenance:
        lines.extend(
            [
                "",
                "Provenance",
                f"  benchmark:           {provenance.get('benchmark')}",
                f"  image_digest:        {provenance.get('image_digest')}",
                f"  git_sha:             {provenance.get('git_sha')}",
                f"  benchmark_def_sha256: {provenance.get('benchmark_def_sha256')}",
                f"  gso_version:         {provenance.get('gso_version')}",
            ]
        )
    lines.extend(
        [
        "",
        summary.get("headline", ""),
        f"verdict: {summary.get('verdict')}",
        "",
        "Measurement",
        f"  likely_noise:              {'yes' if measurement.get('likely_noise') else 'no'}",
        f"  code_changes:              {'yes' if measurement.get('code_changes') else 'no'}",
        f"  statistically_significant: {'yes' if measurement.get('statistically_significant') else 'no'}",
        f"  → {measurement.get('explanation', '')}",
        "",
        "Runtime (geometric mean)",
        f"  baseline:  {_fmt_seconds(runtime.get('baseline'))}",
        f"  optimized: {_fmt_seconds(runtime.get('optimized'))}",
        f"  expert:    {_fmt_seconds(runtime.get('expert'))}",
        "",
        "vs baseline",
        f"  speedup:        {vs_base.get('speedup')}x",
        f"  time_saved_s:   {vs_base.get('time_saved_s')}",
        f"  direction:      {vs_base.get('direction')}",
        "",
        "vs expert",
        f"  parity_percent: {vs_expert.get('parity_percent')}%",
        f"  comparison:     {vs_expert.get('comparison')}",
        f"  matches_expert: {vs_expert.get('matches_expert')}",
        "",
        "expert vs baseline",
        f"  speedup:        {expert_vs_baseline.get('speedup')}x",
        f"  time_saved_s:   {expert_vs_baseline.get('time_saved_s')}",
        "",
        "Eval metrics (GSO harness — do not use external time.time())",
        f"  correctness_passed: {_harness_status(eval_metrics.get('correctness_passed'), yes='yes', no='no')}",
        f"  patch_applied:      {_harness_status(eval_metrics.get('patch_applied'), yes='yes', no='no')}",
        f"  opt_base_passed:    {_format_baseline_opt_line(harness.get('opt_base_passed'), vs_base.get('speedup'))}",
        f"  opt_commit_passed:  {_format_expert_opt_line(harness.get('opt_commit_passed'), vs_expert.get('parity_percent'))}",
        f"  perf_completion:    {eval_metrics.get('perf_tests_passed')}/{eval_metrics.get('perf_tests_total')}"
        if eval_metrics.get("perf_tests_total")
        else "  perf_completion:    n/a",
        f"  memory_measured:    {'yes' if eval_metrics.get('memory_measured') else 'no'}",
        "",
        "Confidence",
        f"  {confidence.get('interpretation', '')}",
        "",
        "Harness",
        f"  tests:                {tests_passed}/{tests_total} passed"
        if tests_total is not None
        else f"  tests:                n/a",
        f"  benchmark_completed:  {_harness_status(run_ok, yes='yes', no='no — run failed or incomplete')}",
        f"  beat_baseline:        {_format_baseline_opt_line(harness.get('opt_base_passed'), vs_base.get('speedup'))}",
        f"  matches_expert:       {_format_expert_opt_line(harness.get('opt_commit_passed'), vs_expert.get('parity_percent'))}",
        "",
        "Results (hub root)",
        f"  {hub_artemis_benchmark_path()}",
        f"  {hub_artemis_test_path()}",
        f"  {hub_summary_path()}",
        ]
    )

    path = hub_summary_path()
    path.write_text("\n".join(lines) + "\n")
    return path


def instance_log_dir(instance_id: str, run_id: str, model_name: str) -> Path:
    safe_model = model_name.replace("/", "__")
    return gso_log_dir() / run_id / safe_model / instance_id


def instance_report_path(
    instance_id: str, run_id: str, model_name: str
) -> Path:
    return instance_log_dir(instance_id, run_id, model_name) / "report.json"


def clean_harness_instance_logs(
    instance_id: str, run_id: str, model_name: str
) -> None:
    """Drop stale per-instance harness artifacts (symlinks, partial test_output)."""
    log_dir = instance_log_dir(instance_id, run_id, model_name)
    if not log_dir.is_dir():
        return
    print(f"Removing stale harness logs: {log_dir}")
    shutil.rmtree(log_dir)


def harness_report_exists(
    instance_id: str, run_id: str, model_name: str
) -> bool:
    return instance_report_path(instance_id, run_id, model_name).is_file()


def default_benchmark_run_id(instance_id: str) -> str:
    return f"benchmark-{instance_id}"


def default_test_run_id(instance_id: str) -> str:
    return f"test-{instance_id}"


def resolve_test_harness_run(
    instance_id: str,
    model_name: str,
    *,
    run_id: str | None = None,
    from_benchmark: bool = False,
    rerun: bool = False,
) -> tuple[str, bool]:
    """Pick test report source: reuse test, else benchmark, else run test harness."""
    test_run_id = default_test_run_id(instance_id)
    benchmark_run_id = default_benchmark_run_id(instance_id)

    if run_id:
        if rerun:
            return run_id, True
        return run_id, not harness_report_exists(instance_id, run_id, model_name)

    if from_benchmark:
        path = instance_report_path(instance_id, benchmark_run_id, model_name)
        if path.is_file():
            print(f"Using benchmark harness report: {path}")
            return benchmark_run_id, False
        raise SystemExit(
            f"No benchmark harness report at {path}.\n"
            f"Run: {harness_command_hint(instance_id, command='benchmark')}"
        )

    if rerun:
        return test_run_id, True

    test_path = instance_report_path(instance_id, test_run_id, model_name)
    if test_path.is_file():
        print(f"Using existing test harness report: {test_path}")
        return test_run_id, False

    benchmark_path = instance_report_path(instance_id, benchmark_run_id, model_name)
    if benchmark_path.is_file():
        print(f"Using benchmark harness report: {benchmark_path}")
        return benchmark_run_id, False

    return test_run_id, True


def _read_instance_report_file(path: Path, instance_id: str) -> dict:
    report = json.loads(path.read_text())
    if instance_id not in report:
        raise SystemExit(f"Instance {instance_id} missing from {path}")
    return report[instance_id]


def load_instance_report(
    instance_id: str, run_id: str, model_name: str, *, command: str = "benchmark"
) -> dict:
    path = instance_report_path(instance_id, run_id, model_name)
    if path.is_file():
        return _read_instance_report_file(path, instance_id)
    log_dir = instance_log_dir(instance_id, run_id, model_name)
    run_log = log_dir / "run_instance.log"
    hint = (
        f"Check {run_log} for grading errors."
        if run_log.is_file()
        else "A prior incomplete run may have left stale logs under logs/run_evaluation/."
    )
    raise SystemExit(
        f"No GSO harness report at {path}.\n"
        f"{hint}\n"
        f"Re-run: {harness_command_hint(instance_id, command=command)}"
    )


def tag_instance_image_for_harness(instance) -> None:
    """Retag the pulled slimshetty image as local gso.eval...:latest for the harness."""
    remote = instance.remote_instance_image_key
    local = instance.instance_image_key
    proc = subprocess.run(
        ["docker", "image", "inspect", remote],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(
            f"Docker image not found: {remote}\n"
            "Run: bash scripts/images.sh pull-images"
        )
    tag_proc = subprocess.run(
        ["docker", "tag", remote, local],
        capture_output=True,
        text=True,
        check=False,
    )
    if tag_proc.returncode != 0:
        raise SystemExit(
            f"Could not tag {remote} as {local}: {tag_proc.stderr.strip()}"
        )


def run_harness(
    instance_id: str,
    *,
    model_name: str = "local-edit",
    run_id: str | None = None,
    timeout: int = 1800,
    max_workers: int = 1,
    pull_image: bool = True,
    ephemeral_image: bool = True,
    action: str = "benchmark",
) -> str:
    instance = load_instance(instance_id)
    require_active_task(instance_id, action=action)
    run_id = run_id or f"local-{instance_id}"
    clean_harness_instance_logs(instance_id, run_id, model_name)
    cleanup_stale_harness_containers(instance_id)
    pred_path = predictions_path(instance_id)
    if not pred_path.exists():
        raise SystemExit(
            f"Missing predictions at {pred_path}. "
            f"Run: {prepare_command_hint(instance_id)}"
        )

    if pull_image:
        verify_instance_image(instance.instance_id, pull=True)
    else:
        verify_instance_image(instance_id, pull=False)
    tag_instance_image_for_harness(instance)

    harness_args = [
        "--dataset_name",
        "gso-bench/gso",
        "--predictions_path",
        str(pred_path),
        "--instance_ids",
        instance_id,
        "--timeout",
        str(timeout),
        "--run_id",
        run_id,
        "--max_workers",
        str(max_workers),
        "--rerun_all",
        "--verbose",
    ]
    cmd = [sys.executable, "-m", "gso.harness.run_evaluation", *harness_args]
    harness_cwd = hub_root().resolve()
    print("Running GSO harness...")
    try:
        env = os.environ.copy()
        defn = _runner_module().load_benchmark_def(benchmark_root(), instance_id)
        timing_iters = (defn.get("runner") or {}).get("timing_iterations")
        if timing_iters and str(timing_iters).isdigit() and int(timing_iters) > 0:
            env["GSO_TIMING_ITERS"] = str(int(timing_iters))
            print(f"Timing iterations: {timing_iters} (from benchmark.yaml)")
        proc = subprocess.run(cmd, cwd=harness_cwd, text=True, check=False, env=env)
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)
    finally:
        if ephemeral_image:
            cleanup_instance_images(instance)
    return run_id


IMPROVEMENT_NOISE_PERCENT = 0.5
EXPERT_MATCH_THRESHOLD = 0.95
BASELINE_OPT_SPEEDUP = 1.2  # GSO MIN_PROB_SPEEDUP — gm speedup required for opt_base
BASELINE_OPT_PERCENT = int(round((BASELINE_OPT_SPEEDUP - 1.0) * 100.0))
EXPERT_MATCH_PERCENT = int(round(EXPERT_MATCH_THRESHOLD * 100.0))
BOOTSTRAP_SAMPLES = 5000
BOOTSTRAP_CI = 0.95


def _slim_runtime_s(
    baseline: float | None,
    optimized: float | None,
    expert: float | None,
) -> dict:
    return {
        k: v
        for k, v in {
            "baseline": baseline,
            "optimized": optimized,
            "expert": expert,
        }.items()
        if v is not None
    }


def _slim_vs_baseline(
    baseline_s: float | None,
    optimized_s: float | None,
    speedup: float | None,
    *,
    significant: bool | None = None,
) -> dict:
    time_saved = (
        round(baseline_s - optimized_s, 6)
        if baseline_s is not None and optimized_s is not None
        else None
    )
    out: dict = {}
    if speedup is not None:
        out["speedup"] = round(speedup, 6)
    if time_saved is not None:
        out["time_saved_s"] = time_saved
    if significant is not None:
        out["significant"] = significant
        out["direction"] = _effective_direction(speedup, significant=significant)
    return out


def _expert_speed_comparison(
    *,
    runtime_ratio: float | None = None,
    gm_vs_expert: float | None = None,
) -> str | None:
    """Human-readable expert vs optimized speed (runtime_ratio = optimized / expert)."""
    if gm_vs_expert is not None:
        if gm_vs_expert >= EXPERT_MATCH_THRESHOLD:
            return "Matches expert speed"
        if gm_vs_expert >= 1.0:
            return f"You are ~{gm_vs_expert:.1f}× faster than expert"
        return f"Expert is ~{1.0 / gm_vs_expert:.1f}× faster than you"
    if runtime_ratio is None:
        return None
    if runtime_ratio <= 1.0 / EXPERT_MATCH_THRESHOLD:
        return "Matches expert speed"
    if runtime_ratio > 1.0:
        return f"Expert is ~{runtime_ratio:.1f}× faster than you"
    return f"You are ~{1.0 / runtime_ratio:.1f}× faster than expert"


def _slim_vs_expert(
    expert_s: float | None,
    optimized_s: float | None,
    *,
    gm_vs_expert: float | None = None,
) -> dict:
    parity = (
        round(gm_vs_expert * 100.0, 2)
        if gm_vs_expert is not None
        else _expert_parity_percent(expert_s, optimized_s)
    )
    ratio = _runtime_ratio_to_expert(optimized_s, expert_s)
    matches = (
        gm_vs_expert >= EXPERT_MATCH_THRESHOLD
        if gm_vs_expert is not None
        else ratio is not None and ratio <= (1.0 / EXPERT_MATCH_THRESHOLD)
    )
    comparison = _expert_speed_comparison(
        runtime_ratio=ratio, gm_vs_expert=gm_vs_expert
    )
    out: dict = {"matches_expert": matches}
    if parity is not None:
        out["parity_percent"] = parity
    if comparison is not None:
        out["comparison"] = comparison
    delta = _seconds_from_expert(expert_s, optimized_s)
    if delta is not None:
        out["time_delta_s"] = delta
    if ratio is not None:
        out["time_ratio"] = ratio  # optimized / expert: >1 means slower than expert
    return out


def _slim_expert_vs_baseline(
    baseline_s: float | None,
    expert_s: float | None,
    speedup: float | None,
) -> dict:
    """Expert vs baseline: speedup = baseline ÷ expert (>1 means expert is faster)."""
    out = _slim_vs_baseline(baseline_s, expert_s, speedup)
    ratio = _runtime_ratio_to_expert(baseline_s, expert_s)  # baseline / expert: same convention as vs_expert.time_ratio
    if ratio is not None:
        out["time_ratio"] = ratio  # baseline / expert: >1 means baseline is slower than expert
    return out


def _slim_memory(mem_stats: dict) -> dict:
    if not mem_stats:
        return {"measured": False}
    baseline_mb = mem_stats.get("baseline_mb") or mem_stats.get("baseline")
    optimized_mb = mem_stats.get("optimized_mb") or mem_stats.get("optimized")
    expert_mb = mem_stats.get("expert_mb") or mem_stats.get("expert")
    if not any(v is not None for v in (baseline_mb, optimized_mb, expert_mb)):
        return {"measured": False}
    out: dict = {"measured": True}
    if baseline_mb is not None:
        out["baseline_mb"] = round(float(baseline_mb), 3)
    if optimized_mb is not None:
        out["optimized_mb"] = round(float(optimized_mb), 3)
    if expert_mb is not None:
        out["expert_mb"] = round(float(expert_mb), 3)
    b, o, e = (
        float(baseline_mb) if baseline_mb is not None else None,
        float(optimized_mb) if optimized_mb is not None else None,
        float(expert_mb) if expert_mb is not None else None,
    )
    if b and o is not None:
        out["vs_baseline_reduction_pct"] = round((b - o) / b * 100, 2)
    if o and e is not None:
        out["vs_expert_parity_pct"] = round(e / o * 100, 2)
    return out


def _confidence_interpretation(
    estimate: float | None,
    ci_low: float | None,
    ci_high: float | None,
    *,
    includes_no_change: bool | None,
    measured_speedup: float | None = None,
    baseline_s: float | None = None,
    optimized_s: float | None = None,
) -> str | None:
    if estimate is None or ci_low is None or ci_high is None:
        return None

    measured_pct = _percent_faster(measured_speedup)
    bootstrap_pct = _percent_faster(estimate)
    pct = measured_pct if measured_pct is not None else bootstrap_pct
    if pct is None:
        return None

    time_saved_s = (
        round(baseline_s - optimized_s, 6)
        if baseline_s is not None and optimized_s is not None
        else None
    )
    speedup = measured_speedup if measured_speedup is not None else estimate

    if includes_no_change:
        if abs(pct) < 0.05:
            change = "the same speed as"
            detail = ""
        elif pct > 0:
            change = f"{abs(pct):.2f}% faster than"
            detail = _format_time_delta(time_saved_s, faster=True)
        else:
            change = f"{abs(pct):.2f}% slower than"
            detail = _format_time_delta(time_saved_s, faster=False)
        ci_lo_pct = _percent_faster(ci_low)
        ci_hi_pct = _percent_faster(ci_high)
        ci_part = (
            f"95% CI for speedup: {ci_low:.3f}×–{ci_high:.3f}×"
            f" ({ci_lo_pct:+.1f}% to {ci_hi_pct:+.1f}% vs baseline)"
            if ci_lo_pct is not None and ci_hi_pct is not None
            else f"95% CI for speedup: {ci_low:.3f}×–{ci_high:.3f}×"
        )
        speedup_part = f" ({speedup:.4f}× speedup)" if speedup is not None else ""
        detail_part = f" — {detail}" if detail else ""
        return (
            f"Optimized is {change} baseline{detail_part}{speedup_part}. "
            f"{ci_part} includes no change — likely measurement noise."
        )
    if estimate > 1.0:
        detail = _format_time_delta(time_saved_s, faster=True)
        detail_part = f" ({detail})" if detail else ""
        return (
            f"Optimized is reliably faster than baseline "
            f"(about {pct:.2f}% on average{detail_part})."
        )
    detail = _format_time_delta(time_saved_s, faster=False)
    detail_part = f" ({detail})" if detail else ""
    return (
        f"Optimized is reliably slower than baseline "
        f"(about {abs(pct):.2f}% on average{detail_part})."
    )


def _format_time_delta(time_saved_s: float | None, *, faster: bool) -> str:
    if time_saved_s is None:
        return ""
    delta = abs(time_saved_s)
    if delta < 1e-9:
        return "no measurable time difference"
    unit = "s"
    value = delta
    if delta < 0.001:
        value = delta * 1_000
        unit = "ms"
    label = "faster" if faster else "slower"
    return f"{value:.3f}{unit} {label} per run"


def _slim_confidence(
    confidence: dict,
    *,
    within_noise: bool,
    tests_faster: int,
    tests_total: int,
    measured_speedup: float | None = None,
    baseline_s: float | None = None,
    optimized_s: float | None = None,
) -> dict:
    ci = confidence.get("ci_95", {})
    low = ci.get("low")
    high = ci.get("high")
    includes_no_change = ci.get("includes_no_change")
    estimate = confidence.get("point_estimate")
    significant = confidence.get("significant_at_95")
    return {
        k: v
        for k, v in {
            "compared_to": "baseline",
            "speedup_ratio_estimate": estimate,
            "speedup_ratio_ci_95_low": low,
            "speedup_ratio_ci_95_high": high,
            "ci_includes_no_change": includes_no_change,
            "statistically_significant": significant,
            "interpretation": _confidence_interpretation(
                estimate,
                low,
                high,
                includes_no_change=includes_no_change,
                measured_speedup=measured_speedup,
                baseline_s=baseline_s,
                optimized_s=optimized_s,
            ),
            "within_measurement_noise": within_noise,
            "tests_faster_than_baseline": (
                f"{tests_faster}/{tests_total}" if tests_total else None
            ),
        }.items()
        if v is not None
    }


def _flatten_timing_samples(times: list[list[float]] | None) -> np.ndarray:
    if not times:
        return np.array([], dtype=float)
    return np.array([t for test in times for t in test], dtype=float)


def _bootstrap_speedup_confidence(
    base_times: list[list[float]] | None,
    patch_times: list[list[float]] | None,
    *,
    n_bootstrap: int = BOOTSTRAP_SAMPLES,
    ci: float = BOOTSTRAP_CI,
) -> dict:
    base_flat = _flatten_timing_samples(base_times)
    patch_flat = _flatten_timing_samples(patch_times)
    if base_flat.size == 0 or patch_flat.size == 0:
        return {}

    rng = np.random.default_rng(0)
    ratios = np.empty(n_bootstrap, dtype=float)
    for i in range(n_bootstrap):
        b = rng.choice(base_flat, size=base_flat.size, replace=True)
        p = rng.choice(patch_flat, size=patch_flat.size, replace=True)
        ratios[i] = b.mean() / p.mean()

    lo, hi = np.percentile(ratios, [(1 - ci) / 2 * 100, (1 + ci) / 2 * 100])
    point = float(base_flat.mean() / patch_flat.mean())
    includes_one = float(lo) <= 1.0 <= float(hi)
    return {
        "point_estimate": round(point, 4),
        "ci_95": {
            "low": round(float(lo), 4),
            "high": round(float(hi), 4),
            "includes_no_change": includes_one,
        },
        "significant_at_95": not includes_one,
        "method": "unpaired_bootstrap_over_all_timing_samples",
        "n_baseline_samples": int(base_flat.size),
        "n_optimized_samples": int(patch_flat.size),
    }


def _speedup_direction(speedup: float | None) -> str | None:
    if speedup is None:
        return None
    if abs(speedup - 1.0) < 1e-9:
        return "unchanged"
    return "faster" if speedup > 1.0 else "slower"


def _is_placeholder_patch(instance_id: str) -> bool:
    """True when patch.diff is only the compile-time gso-placeholder comment marker."""
    patch_path = workspace_dir(instance_id) / "patch.diff"
    if not patch_path.exists():
        return False
    text = patch_path.read_text()
    if not any(marker in text for marker in GSO_PLACEHOLDER_MARKERS):
        return False
    changed = [
        line
        for line in text.splitlines()
        if line.startswith(("+", "-"))
        and not line.startswith(("+++", "---"))
    ]
    return len(changed) <= 2


def _workspace_files_unchanged(instance_id: str) -> bool:
    meta = load_metadata(instance_id)
    baseline_dir = workspace_dir(instance_id) / "baseline"
    work_dir = project_root()
    if not baseline_dir.is_dir() or not work_dir.is_dir():
        return False
    for rel_path in patch_file_list(meta) or meta.get("files", []):
        left = baseline_dir / rel_path
        right = work_dir / rel_path
        if not left.exists() or not right.exists():
            return False
        proc = subprocess.run(
            ["diff", "-q", str(left), str(right)],
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0:
            return False
    return True


def _patch_metadata(instance_id: str) -> dict:
    placeholder = _is_placeholder_patch(instance_id)
    unchanged = _workspace_files_unchanged(instance_id)
    if placeholder or unchanged:
        return {"patch_type": "unchanged", "code_changes": False}
    return {"patch_type": "real_edit", "code_changes": True}


def _patch_is_placeholder(patch_meta: dict) -> bool:
    return patch_meta.get("patch_type") in {"unchanged", "placeholder"} or not patch_meta.get(
        "code_changes", True
    )


def _within_measurement_noise(
    gm_patch_base: float | None,
    gsd_patch_base: float | None,
    confidence: dict,
    pct_faster: float | None,
) -> bool:
    if confidence.get("ci_95", {}).get("includes_no_change") is True:
        return True
    if confidence.get("includes_no_change") is True:
        return True
    if confidence.get("significant_at_95"):
        return False
    if gm_patch_base and gsd_patch_base and gsd_patch_base > 0:
        return abs(np.log(gm_patch_base)) < np.log(gsd_patch_base)
    return pct_faster is not None and abs(pct_faster) < IMPROVEMENT_NOISE_PERCENT


def _percent_faster(speedup: float | None) -> float | None:
    if speedup is None:
        return None
    return round((speedup - 1.0) * 100.0, 2)


def _expert_parity_percent(
    expert_seconds: float | None, your_seconds: float | None
) -> float | None:
    if expert_seconds is None or your_seconds is None or your_seconds <= 0:
        return None
    return round(expert_seconds / your_seconds * 100.0, 2)


def _seconds_from_expert(
    expert_seconds: float | None, your_seconds: float | None
) -> float | None:
    if expert_seconds is None or your_seconds is None:
        return None
    return round(your_seconds - expert_seconds, 6)


def _runtime_ratio_to_expert(
    your_seconds: float | None, expert_seconds: float | None
) -> float | None:
    if your_seconds is None or expert_seconds is None or expert_seconds <= 0:
        return None
    return round(your_seconds / expert_seconds, 4)


def _effective_direction(
    speedup: float | None, *, significant: bool | None
) -> str | None:
    """Point-estimate direction misleads when not significant — report unchanged."""
    if speedup is None:
        return None
    if significant is False:
        return "unchanged"
    return _speedup_direction(speedup)


def _measurement_assessment(
    *,
    within_noise: bool,
    significant: bool,
    no_code_changes: bool,
    confidence: dict,
    pct_faster: float | None,
) -> dict:
    """Plain-language noise vs real-change assessment."""
    ci = confidence.get("ci_95", {})
    ci_includes_one = ci.get("includes_no_change", True)
    likely_noise = within_noise or not significant or no_code_changes

    if no_code_changes:
        explanation = (
            "Baseline and optimized code are the same. "
            "Any timing difference is harness measurement noise, not a real optimization."
        )
    elif likely_noise or ci_includes_one:
        pct = abs(pct_faster or 0)
        explanation = (
            f"The observed {pct:.2f}% change vs baseline is not statistically "
            "significant (95% CI includes no change). Treat as measurement noise."
        )
    elif significant and pct_faster is not None and pct_faster > 0:
        explanation = (
            f"The {pct_faster:.2f}% speedup vs baseline is statistically significant "
            "at 95% — likely a real improvement."
        )
    elif significant and pct_faster is not None and pct_faster < 0:
        explanation = (
            f"The {abs(pct_faster):.2f}% slowdown vs baseline is statistically "
            "significant at 95% — likely a real regression."
        )
    else:
        explanation = "Insufficient data to assess whether the change is noise."

    return {
        "likely_noise": likely_noise,
        "code_changes": not no_code_changes,
        "statistically_significant": significant,
        "ci_includes_no_change": ci_includes_one,
        "explanation": explanation,
    }


def _improvement_verdict(
    gm_patch_base: float | None,
    gm_patch_commit: float | None,
    *,
    within_noise: bool,
    significant: bool,
    is_placeholder: bool,
) -> str:
    if gm_patch_base is None:
        return "unavailable"
    if within_noise or (is_placeholder and not significant):
        if gm_patch_commit is not None and gm_patch_commit >= EXPERT_MATCH_THRESHOLD:
            return "no_change_near_expert"
        return "no_change"
    if gm_patch_base < 1.0:
        return "slower_than_baseline" if significant else "no_change"
    if gm_patch_commit is not None and gm_patch_commit >= EXPERT_MATCH_THRESHOLD:
        return "improved_matches_expert"
    if gm_patch_commit is not None and gm_patch_commit < EXPERT_MATCH_THRESHOLD:
        return "improved_below_expert"
    return "improved"


def _improvement_headline(
    gm_patch_base: float | None,
    gm_patch_commit: float | None,
    *,
    within_noise: bool,
    significant: bool,
    patch_meta: dict,
    tests_faster: int,
    tests_total: int,
) -> str:
    pct_faster = _percent_faster(gm_patch_base)
    if gm_patch_base is None:
        return "Benchmark comparison unavailable."

    if _patch_is_placeholder(patch_meta) or not patch_meta.get("code_changes", True):
        if within_noise or not significant:
            return (
                "No measurable improvement vs baseline. "
                "Likely measurement noise (unchanged code)."
            )

    if within_noise or not significant:
        near_expert = (
            gm_patch_commit is not None and gm_patch_commit >= EXPERT_MATCH_THRESHOLD
        )
        if near_expert:
            return (
                "No statistically significant change vs baseline. "
                "Likely measurement noise. Already near expert speed."
            )
        return (
            "No statistically significant change vs baseline. "
            "Likely measurement noise."
        )

    if gm_patch_base < 1.0:
        return (
            f"Patch is significantly slower than baseline "
            f"({abs(pct_faster or 0):.1f}% slower)."
        )
    if gm_patch_commit is not None and gm_patch_commit >= EXPERT_MATCH_THRESHOLD:
        return (
            f"Patch is significantly {pct_faster:.1f}% faster than baseline "
            f"({tests_faster}/{tests_total} tests faster) and matches expert."
        )
    if gm_patch_commit is not None:
        comparison = _expert_speed_comparison(gm_vs_expert=gm_patch_commit)
        return (
            f"Patch is significantly {pct_faster:.1f}% faster than baseline, "
            f"but {comparison.lower()}."
        )
    return (
        f"Patch is significantly {pct_faster:.1f}% faster than baseline"
        f" ({tests_faster}/{tests_total} tests faster)."
    )


def _harness_eval_metrics(
    instance_report: dict,
    *,
    harness: dict | None = None,
) -> dict:
    """Correctness, Opt@1 gates, and perf completion from the GSO harness report.

    Wall-clock timings and speedups must come from this harness (timeit microbenchmarks
    inside the task Docker image) — not from external time.time() wrappers.
    """
    harness = harness or {}
    tests_total = int(harness.get("tests_total") or 0)
    tests_passed = int(harness.get("tests_passed") or 0)
    if not tests_total:
        per_test = (instance_report.get("time_stats") or {}).get(
            "per_test_means", {}
        ).get("base") or instance_report.get("base_times") or []
        tests_total = len(per_test)
        if instance_report.get("test_passed") and tests_total:
            tests_passed = tests_total

    perf_completion_rate = (
        round(100.0 * tests_passed / tests_total, 2)
        if tests_total > 0
        else None
    )

    mem_stats = instance_report.get("memory_stats") or {}
    memory_measured = bool(mem_stats)

    metrics: dict = {
        "timing_source": "gso_harness",
        "correctness_passed": instance_report.get("test_passed"),
        "patch_applied": instance_report.get("patch_successfully_applied"),
        "harness_ran": instance_report.get("base_successfully_run"),
        "opt_base_passed": instance_report.get("opt_base"),
        "opt_commit_passed": instance_report.get("opt_commit"),
        "perf_tests_passed": tests_passed if tests_total else None,
        "perf_tests_total": tests_total or None,
        "perf_completion_rate": perf_completion_rate,
        "memory_measured": memory_measured,
    }

    if memory_measured:
        for role, dst_key in (
            ("baseline", "memory_mb_baseline"),
            ("optimized", "memory_mb_optimized"),
            ("expert", "memory_mb_expert"),
        ):
            value = mem_stats.get(role)
            if value is None:
                value = mem_stats.get(f"{role}_mb")
            if value is not None:
                metrics[dst_key] = round(float(value), 3)

    return metrics


def _numeric_eval_metrics(metrics: dict) -> dict[str, int | float]:
    out: dict[str, int | float] = {}
    for key in (
        "correctness_passed",
        "patch_applied",
        "harness_ran",
        "opt_base_passed",
        "memory_measured",
    ):
        if metrics.get(key) is not None:
            out[key] = _bool_num(metrics[key])
    rate = _finite_scalar(metrics.get("perf_completion_rate"))
    if rate is not None:
        out["perf_completion_rate"] = rate
    for key in (
        "memory_mb_baseline",
        "memory_mb_optimized",
        "memory_mb_expert",
    ):
        value = _finite_scalar(metrics.get(key))
        if value is not None:
            out[key] = value
    return out


def _slim_latency(instance_report: dict) -> dict | None:
    """P50/P95/P99 distribution across all raw timing samples for each variant."""

    def _pct(raw: list[list[float]] | None) -> dict | None:
        if not raw:
            return None
        flat = [t for group in raw for t in group if t is not None]
        if not flat:
            return None
        arr = np.array(flat)
        return {
            "p50_ms": round(float(np.percentile(arr, 50)) * 1000, 3),
            "p75_ms": round(float(np.percentile(arr, 75)) * 1000, 3),
            "p95_ms": round(float(np.percentile(arr, 95)) * 1000, 3),
            "p99_ms": round(float(np.percentile(arr, 99)) * 1000, 3),
            "min_ms": round(float(arr.min()) * 1000, 3),
            "max_ms": round(float(arr.max()) * 1000, 3),
            "n_samples": len(flat),
        }

    result = {}
    for raw_key, label in (
        ("base_times", "baseline"),
        ("patch_times", "optimized"),
        ("commit_times", "expert"),
    ):
        pct = _pct(instance_report.get(raw_key))
        if pct:
            result[label] = pct

    if not result:
        return None

    opt = result.get("optimized") or {}
    if opt.get("p50_ms") and opt.get("p99_ms") and opt["p50_ms"] > 0:
        result["optimized_p99_vs_p50"] = round(opt["p99_ms"] / opt["p50_ms"], 3)

    return result


def _slim_per_test(instance_report: dict) -> dict | None:
    """Per-test means (ms) for baseline, optimized, expert + per-test speedup."""
    per_test_means = (instance_report.get("time_stats") or {}).get("per_test_means") or {}
    base = per_test_means.get("base") or []
    patch = per_test_means.get("patch") or []
    commit = per_test_means.get("commit") or []
    if not base:
        return None
    n = len(base)

    def _ms(v: float) -> float:
        return round(v * 1000, 3)

    result: dict = {
        "n_tests": n,
        "baseline_ms": [_ms(v) for v in base],
    }
    if patch and len(patch) == n:
        result["optimized_ms"] = [_ms(v) for v in patch]
        speedups_opt = []
        for b, p in zip(base, patch):
            speedups_opt.append(round(b / p, 4) if p and p > 0 else None)
        result["speedup_vs_baseline"] = speedups_opt
    if commit and len(commit) == n:
        result["expert_ms"] = [_ms(v) for v in commit]
        speedups_exp = []
        for b, c in zip(base, commit):
            speedups_exp.append(round(b / c, 4) if c and c > 0 else None)
        result["expert_speedup_vs_baseline"] = speedups_exp
    if patch and commit and len(patch) == n and len(commit) == n:
        speedups_vs_exp = []
        for p, c in zip(patch, commit):
            speedups_vs_exp.append(round(p / c, 4) if c and c > 0 else None)
        result["optimized_time_ratio_vs_expert"] = speedups_vs_exp
    return result


def build_improvement_summary(
    instance_report: dict, *, instance_id: str | None = None
) -> dict:
    """Human-readable baseline → optimized comparison (ratios, not machine-absolute)."""
    time_stats = instance_report.get("time_stats", {})
    opt_stats = instance_report.get("opt_stats", {})
    base_mean = time_stats.get("base_mean")
    patch_mean = time_stats.get("patch_mean")
    commit_mean = time_stats.get("commit_mean")
    gm_patch_base = opt_stats.get("gm_speedup_patch_base")
    gm_patch_commit = opt_stats.get("gm_speedup_patch_commit")
    gm_commit_base = opt_stats.get("gm_speedup_commit_base")
    gsd_patch_base = opt_stats.get("gsd_speedup_patch_base")

    base_times = instance_report.get("base_times")
    patch_times = instance_report.get("patch_times")
    confidence = _bootstrap_speedup_confidence(base_times, patch_times)
    significant = confidence.get("significant_at_95", False)
    pct_faster = _percent_faster(gm_patch_base)
    within_noise = _within_measurement_noise(
        gm_patch_base, gsd_patch_base, confidence, pct_faster
    )

    patch_meta = _patch_metadata(instance_id) if instance_id else {}
    no_code_changes = not patch_meta.get("code_changes", True)
    patch_base_speedups = (
        opt_stats.get("per_test_speedups", {}).get("patch_base_speedups", []) or []
    )

    tests_faster = sum(1 for s in patch_base_speedups if s and s > 1.0)
    per_test_means = time_stats.get("per_test_means") or {}
    tests_total = (
        len(patch_base_speedups)
        or len(per_test_means.get("base") or [])
        or len(instance_report.get("base_times") or [])
    )
    all_tests_ok = bool(instance_report.get("test_passed"))
    tests_passed_count = tests_total if all_tests_ok and tests_total else 0
    headline = _improvement_headline(
        gm_patch_base,
        gm_patch_commit,
        within_noise=within_noise,
        significant=significant,
        patch_meta=patch_meta,
        tests_faster=tests_faster,
        tests_total=tests_total,
    )
    verdict = _improvement_verdict(
        gm_patch_base,
        gm_patch_commit,
        within_noise=within_noise,
        significant=significant,
        is_placeholder=_patch_is_placeholder(patch_meta),
    )

    confidence_block = _slim_confidence(
        confidence,
        within_noise=within_noise,
        tests_faster=tests_faster,
        tests_total=tests_total,
        measured_speedup=gm_patch_base,
        baseline_s=base_mean,
        optimized_s=patch_mean,
    )
    measurement = _measurement_assessment(
        within_noise=within_noise,
        significant=significant,
        no_code_changes=no_code_changes,
        confidence=confidence,
        pct_faster=pct_faster,
    )
    vs_baseline_sig = False if no_code_changes else significant

    harness_block = {
        "tests_passed": tests_passed_count,
        "tests_total": tests_total,
        "opt_base_passed": instance_report.get("opt_base"),
        "opt_commit_passed": instance_report.get("opt_commit"),
    }

    return {
        "patch": {
            "patch_type": patch_meta.get("patch_type"),
            "code_changes": patch_meta.get("code_changes", True),
        },
        "summary": {
            "headline": headline,
            "verdict": verdict,
            "measurement": measurement,
            "runtime_s": _slim_runtime_s(base_mean, patch_mean, commit_mean),
            "vs_baseline": _slim_vs_baseline(
                base_mean,
                patch_mean,
                gm_patch_base,
                significant=vs_baseline_sig,
            ),
            "vs_expert": _slim_vs_expert(
                commit_mean, patch_mean, gm_vs_expert=gm_patch_commit
            ),
            "expert_vs_baseline": _slim_expert_vs_baseline(
                base_mean, commit_mean, gm_commit_base
            ),
            "memory": _slim_memory(instance_report.get("memory_stats") or {}),
            "confidence": confidence_block,
            "harness": harness_block,
            "eval": _harness_eval_metrics(
                instance_report, harness=harness_block
            ),
            **({"latency": lt} if (lt := _slim_latency(instance_report)) else {}),
            **({"per_test": pt} if (pt := _slim_per_test(instance_report)) else {}),
        },
    }


def build_artemis_benchmark_payload(
    instance_id: str,
    run_id: str,
    model_name: str,
    instance_report: dict,
) -> dict:
    parts = build_improvement_summary(instance_report, instance_id=instance_id)
    recorded_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "instance_id": instance_id,
        "run_id": run_id,
        "model_name": model_name,
        "recorded_at": recorded_at,
        "patch": parts["patch"],
        "summary": parts["summary"],
        "harness_report": str(
            instance_report_path(instance_id, run_id, model_name)
        ),
    }
    if provenance := build_provenance(instance_id, recorded_at):
        payload["provenance"] = provenance
    return payload


_VERDICT_TO_INT = {
    "unavailable": -1,
    "no_change": 0,
    "no_change_near_expert": 1,
    "slower_than_baseline": 2,
    "improved_matches_expert": 3,
    "improved_below_expert": 4,
    "improved": 5,
}

def _task_index(instance_id: str) -> int:
    ids = _runner_module().list_instance_ids(benchmark_root())
    try:
        return ids.index(instance_id)
    except ValueError:
        return int(hashlib.sha256(instance_id.encode()).hexdigest()[:8], 16)


def _run_id_numeric(run_id: str) -> int:
    digest = int(hashlib.sha256(run_id.encode()).hexdigest()[:8], 16)
    return digest % 10000


def _recorded_at_numeric(recorded_at: str) -> float:
    dt = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
    return round(dt.timestamp(), 6)


def _bool_num(value: Any) -> int:
    return int(bool(value))


def _numericize_confidence(confidence: dict | None) -> dict:
    if not confidence:
        return {}
    out: dict[str, int | float] = {}
    for key, value in confidence.items():
        if key in {"interpretation", "compared_to", "tests_faster_than_baseline"}:
            continue
        if isinstance(value, bool):
            out[key] = _bool_num(value)
        elif isinstance(value, (int, float)):
            out[key] = value
    return out


def _finite_scalar(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return round(value, 6)
    return None


def build_artemis_benchmark_payload_numeric(
    instance_id: str,
    run_id: str,
    model_name: str,
    instance_report: dict,
) -> dict[str, int | float]:
    """Flat Artemis JSON: headline metrics only (no per-test or raw timings)."""
    del model_name  # single model in this hub; omitted from numeric export
    parts = build_improvement_summary(instance_report, instance_id=instance_id)
    recorded_at = datetime.now(timezone.utc).isoformat()
    summary = parts["summary"]
    patch = parts["patch"]
    harness = summary.get("harness") or {}
    runtime = summary.get("runtime_s") or {}
    vs_base = summary.get("vs_baseline") or {}
    vs_expert = summary.get("vs_expert") or {}
    expert_vs_baseline = summary.get("expert_vs_baseline") or {}
    conf = _numericize_confidence(summary.get("confidence"))

    out: dict[str, int | float] = {
        "task": _task_index(instance_id),
        "run_id": _run_id_numeric(run_id),
        "recorded_at": _recorded_at_numeric(recorded_at),
        "code_changes": _bool_num(patch.get("code_changes")),
        "verdict": _VERDICT_TO_INT.get(str(summary.get("verdict")), -1),
        "tests_passed": int(harness.get("tests_passed") or 0),
        "tests_total": int(harness.get("tests_total") or 0),
    }

    for key in ("baseline", "optimized", "expert"):
        value = _finite_scalar(runtime.get(key))
        if value is not None:
            out[f"runtime_s_{key}"] = value

    value = _finite_scalar(vs_base.get("speedup"))
    if value is not None:
        out["vs_baseline_speedup"] = value
    if vs_base.get("significant") is not None:
        out["vs_baseline_significant"] = _bool_num(vs_base.get("significant"))

    value = _finite_scalar(vs_expert.get("parity_percent"))
    if value is not None:
        out["vs_expert_parity_percent"] = value

    value = _finite_scalar(expert_vs_baseline.get("speedup"))
    if value is not None:
        out["expert_vs_baseline_speedup"] = value

    for src_key, dst_key in (
        ("speedup_ratio_estimate", "confidence_speedup_ratio_estimate"),
        ("speedup_ratio_ci_95_low", "confidence_speedup_ratio_ci_95_low"),
        ("speedup_ratio_ci_95_high", "confidence_speedup_ratio_ci_95_high"),
        ("statistically_significant", "confidence_statistically_significant"),
        ("ci_includes_no_change", "confidence_ci_includes_no_change"),
        ("within_measurement_noise", "confidence_within_measurement_noise"),
    ):
        if src_key not in conf:
            continue
        value = conf[src_key]
        if isinstance(value, (int, float)):
            out[dst_key] = value

    out.update(
        _numeric_eval_metrics(
            summary.get("eval")
            or _harness_eval_metrics(instance_report, harness=harness)
        )
    )

    # GSO paper primary metrics (https://arxiv.org/abs/2505.23671)
    # opt_at_1: Opt@1 at default threshold p=0.95 (agent achieves ≥95% of expert speedup)
    out["opt_at_1"] = _bool_num(instance_report.get("opt_commit"))
    opt_stats = instance_report.get("opt_stats") or {}
    gm_patch_commit = opt_stats.get("gm_speedup_patch_commit") or 0.0
    gm_patch_base = opt_stats.get("gm_speedup_patch_base") or 0.0
    correctness = bool(instance_report.get("test_passed"))
    # Patch must beat baseline by BASELINE_OPT_SPEEDUP (1.2x) to count as a real optimisation.
    # Matching expert timing without improving over baseline is not an opt (e.g. unchanged code).
    beats_baseline = round(gm_patch_base, 1) >= BASELINE_OPT_SPEEDUP
    # opt_p_at_1_p{N}: Opt_p@1 for threshold p=N/100 (correctness + beats baseline + ≥p% of expert speed)
    for suffix, threshold in [
        ("p0", 0.0), ("p10", 0.1), ("p20", 0.2), ("p30", 0.3),
        ("p40", 0.4), ("p50", 0.5), ("p60", 0.6), ("p70", 0.7),
        ("p80", 0.8), ("p90", 0.9), ("p95", 0.95), ("p100", 1.0),
    ]:
        if threshold == 0.0:
            out[f"opt_p_at_1_{suffix}"] = int(correctness)
        else:
            out[f"opt_p_at_1_{suffix}"] = int(correctness and beats_baseline and gm_patch_commit >= threshold)

    # --- Runtime depth (variance / measurement quality) ---
    time_stats_raw = instance_report.get("time_stats") or {}
    base_std = _finite_scalar(time_stats_raw.get("base_std"))
    patch_std = _finite_scalar(time_stats_raw.get("patch_std"))
    per_test_patch_means = (time_stats_raw.get("per_test_means") or {}).get("patch") or []
    if base_std is not None:
        out["runtime_s_baseline_stddev"] = base_std
    if patch_std is not None:
        out["runtime_s_optimized_stddev"] = patch_std
    finite_patch = [t for t in per_test_patch_means if t is not None and math.isfinite(t)]
    if finite_patch:
        out["runtime_s_optimized_min"] = round(min(finite_patch), 6)

    # --- Gap to expert (absolute and multiplicative distance remaining) ---
    opt_s = runtime.get("optimized")
    exp_s = runtime.get("expert")
    if (opt_s is not None and exp_s is not None
            and math.isfinite(float(opt_s)) and math.isfinite(float(exp_s)) and float(exp_s) > 0):
        gap = _finite_scalar(float(opt_s) - float(exp_s))
        if gap is not None:
            out["runtime_s_gap_to_expert"] = gap

    # --- Memory metrics (-1 = not measured in this run) ---
    mem_stats_raw = instance_report.get("memory_stats") or {}
    if mem_stats_raw:
        for role, key in (
            ("baseline", "memory_mb_baseline"),
            ("optimized", "memory_mb_optimized"),
            ("expert", "memory_mb_expert"),
        ):
            v = mem_stats_raw.get(role) or mem_stats_raw.get(f"{role}_mb")
            out[key] = round(float(v), 3) if v is not None else -1
        mb_b, mb_o, mb_e = out.get("memory_mb_baseline", -1), out.get("memory_mb_optimized", -1), out.get("memory_mb_expert", -1)
        out["vs_baseline_memory_reduction_pct"] = (
            round((mb_b - mb_o) / mb_b * 100, 2) if mb_b > 0 and mb_o >= 0 else -1
        )
        out["vs_expert_memory_parity_pct"] = (
            round(mb_e / mb_o * 100, 2) if mb_o > 0 and mb_e >= 0 else -1
        )
    else:
        out["memory_mb_baseline"] = -1
        out["memory_mb_optimized"] = -1
        out["memory_mb_expert"] = -1
        out["vs_baseline_memory_reduction_pct"] = -1
        out["vs_expert_memory_parity_pct"] = -1

    return out


def build_artemis_test_payload(
    instance_id: str,
    run_id: str,
    model_name: str,
    instance_report: dict,
) -> dict:
    opt_stats = instance_report.get("opt_stats", {})
    recorded_at = datetime.now(timezone.utc).isoformat()
    harness_stub = {
        "tests_passed": len(
            (instance_report.get("time_stats") or {})
            .get("per_test_means", {})
            .get("base", [])
            or instance_report.get("base_times")
            or []
        )
        if instance_report.get("test_passed")
        else 0,
        "tests_total": len(
            (instance_report.get("time_stats") or {})
            .get("per_test_means", {})
            .get("base", [])
            or instance_report.get("base_times")
            or []
        ),
    }
    eval_metrics = _harness_eval_metrics(instance_report, harness=harness_stub)
    payload = {
        "instance_id": instance_id,
        "run_id": run_id,
        "model_name": model_name,
        "recorded_at": recorded_at,
        "patch_exists": instance_report.get("patch_exists"),
        "patch_successfully_applied": instance_report.get(
            "patch_successfully_applied"
        ),
        "base_successfully_run": instance_report.get("base_successfully_run"),
        "test_passed": instance_report.get("test_passed"),
        "passed": instance_report.get("test_passed"),
        "opt": {
            "base": instance_report.get("opt_base"),
            "commit": instance_report.get("opt_commit"),
            "main": instance_report.get("opt_main"),
        },
        "speedups": {
            "gm_patch_base": opt_stats.get("gm_speedup_patch_base"),
            "gm_patch_commit": opt_stats.get("gm_speedup_patch_commit"),
            "gm_patch_main": opt_stats.get("gm_speedup_patch_main"),
            "gm_commit_base": opt_stats.get("gm_speedup_commit_base"),
        },
        "percent_of_expert": {
            "baseline": (
                round(100.0 / opt_stats["gm_speedup_commit_base"], 2)
                if opt_stats.get("gm_speedup_commit_base")
                else None
            ),
            "optimized": (
                round(opt_stats["gm_speedup_patch_commit"] * 100.0, 2)
                if opt_stats.get("gm_speedup_patch_commit") is not None
                else None
            ),
        },
        "eval": eval_metrics,
        "harness_report": str(
            instance_report_path(instance_id, run_id, model_name)
        ),
    }
    if provenance := build_provenance(instance_id, recorded_at):
        payload["provenance"] = provenance
    return payload


def write_benchmark_json(
    instance_id: str,
    run_id: str,
    model_name: str = "local-edit",
) -> Path:
    instance_report = load_instance_report(instance_id, run_id, model_name)
    robust_out = hub_artemis_benchmark_robust_path()
    numeric_out = hub_artemis_benchmark_path()
    robust_payload = build_artemis_benchmark_payload(
        instance_id, run_id, model_name, instance_report
    )
    numeric_payload = build_artemis_benchmark_payload_numeric(
        instance_id, run_id, model_name, instance_report
    )
    robust_out.write_text(json.dumps(robust_payload, indent=2))
    numeric_out.write_text(json.dumps(numeric_payload, indent=2))
    print(f"Wrote benchmark results (robust): {robust_out}")
    print(f"Wrote benchmark results (numeric): {numeric_out}")
    task_out = workspace_dir(instance_id) / "output"
    task_out.mkdir(parents=True, exist_ok=True)
    (task_out / ARTEMIS_BENCHMARK_ROBUST_FILENAME).write_text(json.dumps(robust_payload, indent=2))
    (task_out / ARTEMIS_BENCHMARK_FILENAME).write_text(json.dumps(numeric_payload, indent=2))
    print(f"Wrote task output: {task_out}")
    write_comparison_summary(instance_id, run_id, model_name)
    summary_path = hub_summary_path()
    if summary_path.exists():
        print(f"Wrote comparison summary: {summary_path}")
    return numeric_out


def write_test_json(
    instance_id: str,
    run_id: str,
    model_name: str = "local-edit",
) -> Path:
    instance_report = load_instance_report(
        instance_id, run_id, model_name, command="test"
    )
    out = hub_artemis_test_path()
    payload = build_artemis_test_payload(
        instance_id, run_id, model_name, instance_report
    )
    out.write_text(json.dumps(payload, indent=2))
    print(f"Wrote test results: {out}")
    return out


def benchmark_patch(
    instance_id: str,
    *,
    model_name: str = "local-edit",
    run_id: str | None = None,
    timeout: int = 1800,
    max_workers: int = 1,
    pull_image: bool = True,
    ephemeral_image: bool = True,
    reuse_report: bool = False,
) -> Path:
    require_active_task(instance_id, action="benchmark")
    run_id = run_id or f"benchmark-{instance_id}"
    report_path = instance_report_path(instance_id, run_id, model_name)
    if not reuse_report or not report_path.is_file():
        run_harness(
            instance_id,
            model_name=model_name,
            run_id=run_id,
            timeout=timeout,
            max_workers=max_workers,
            pull_image=pull_image,
            ephemeral_image=ephemeral_image,
            action="benchmark",
        )
    return write_benchmark_json(instance_id, run_id, model_name)


def test_patch(
    instance_id: str,
    *,
    model_name: str = "local-edit",
    run_id: str | None = None,
    timeout: int = 1800,
    max_workers: int = 1,
    pull_image: bool = True,
    ephemeral_image: bool = True,
    from_benchmark: bool = False,
    rerun: bool = False,
) -> Path:
    require_active_task(instance_id, action="test")
    run_id, needs_run = resolve_test_harness_run(
        instance_id,
        model_name,
        run_id=run_id,
        from_benchmark=from_benchmark,
        rerun=rerun,
    )
    if needs_run:
        run_harness(
            instance_id,
            model_name=model_name,
            run_id=run_id,
            timeout=timeout,
            max_workers=max_workers,
            pull_image=pull_image,
            ephemeral_image=ephemeral_image,
            action="test",
        )
    return write_test_json(instance_id, run_id, model_name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local project/ + eval/baseline workflow for GSO."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    setup = sub.add_parser("setup", help="Create workspace with baseline/ and edit tree")
    setup.add_argument("instance_id")
    setup.add_argument(
        "--files",
        nargs="+",
        help="Repo-relative file paths to copy (default: files touched in gt_diff)",
    )
    setup.add_argument("--force", action="store_true", help="Recreate workspace")
    setup.add_argument(
        "--include-tests",
        action="store_true",
        help="Also copy test files from gt_diff (default: source files only)",
    )

    reset = sub.add_parser(
        "reset",
        help="Restore editable files from baseline/ (discard edits in project/)",
    )
    reset.add_argument("instance_id")

    patch = sub.add_parser("patch", help="Build patch.diff and predictions.jsonl")
    patch.add_argument("instance_id")
    patch.add_argument("--model-name", default="local-edit")
    patch.add_argument(
        "--placeholder-on-unchanged",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "When baseline/ and project/ are identical (no code changes), emit an "
            "automatic gso-placeholder comment in patch.diff (default: on)"
        ),
    )

    benchmark = sub.add_parser(
        "benchmark", help="Run performance benchmark and save benchmark JSON"
    )
    benchmark.add_argument("instance_id")
    benchmark.add_argument("--model-name", default="local-edit")
    benchmark.add_argument("--run-id")
    benchmark.add_argument("--timeout", type=int, default=1800)
    benchmark.add_argument("--max-workers", type=int, default=1)
    benchmark.add_argument("--no-pull", action="store_true")
    benchmark.add_argument(
        "--keep-image",
        action="store_true",
        help="Keep the Docker image after grading (default: remove to save disk)",
    )
    benchmark.add_argument(
        "--reuse-report",
        action="store_true",
        help="Skip harness if report already exists for this run-id",
    )

    test_cmd = sub.add_parser(
        "test",
        help="Write tests_artemis_results.json (reuse harness report or run test harness)",
    )
    test_cmd.add_argument("instance_id")
    test_cmd.add_argument("--model-name", default="local-edit")
    test_cmd.add_argument("--run-id")
    test_cmd.add_argument("--timeout", type=int, default=1800)
    test_cmd.add_argument("--max-workers", type=int, default=1)
    test_cmd.add_argument("--no-pull", action="store_true")
    test_cmd.add_argument(
        "--keep-image",
        action="store_true",
        help="Keep the Docker image after grading (default: remove to save disk)",
    )
    test_cmd.add_argument(
        "--rerun",
        action="store_true",
        help="Force a fresh test harness run (run-id test-<task>)",
    )
    test_cmd.add_argument(
        "--from-benchmark",
        action="store_true",
        help="Use only the benchmark harness report; error if missing",
    )

    args = parser.parse_args()
    if hasattr(args, "keep_image"):
        args.ephemeral_image = not args.keep_image
    else:
        args.ephemeral_image = True

    if args.command == "setup":
        setup_workspace(
            args.instance_id,
            args.files,
            force=args.force,
            include_tests=args.include_tests,
        )
    elif args.command == "reset":
        reset_workspace_edits(args.instance_id)
    elif args.command == "patch":
        build_patch(
            args.instance_id,
            model_name=args.model_name,
            placeholder_on_unchanged=args.placeholder_on_unchanged,
        )
    elif args.command == "benchmark":
        benchmark_patch(
            args.instance_id,
            model_name=args.model_name,
            run_id=args.run_id,
            timeout=args.timeout,
            max_workers=args.max_workers,
            pull_image=not args.no_pull,
            ephemeral_image=args.ephemeral_image,
            reuse_report=args.reuse_report,
        )
    elif args.command == "test":
        test_patch(
            args.instance_id,
            model_name=args.model_name,
            run_id=args.run_id,
            timeout=args.timeout,
            max_workers=args.max_workers,
            pull_image=not args.no_pull,
            ephemeral_image=args.ephemeral_image,
            from_benchmark=args.from_benchmark,
            rerun=args.rerun,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
