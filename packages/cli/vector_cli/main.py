import hashlib
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from stat import S_ISREG
from typing import Annotated, NoReturn
from uuid import UUID

import typer

from vector_cli.client import SyncManifestEntry, SyncPlanResult, VectorApiError, VectorClient

SUPPORTED_EXTENSIONS = {"txt", "md", "pdf", "docx"}
DEFAULT_MAX_BYTES = 10485760
DEFAULT_URL = "http://localhost:8000"
DEFAULT_SEARCH_LIMIT = 5
MAX_SEARCH_LIMIT = 50
HASH_CHUNK_BYTES = 1024 * 1024

app = typer.Typer(help="Vector knowledge base CLI.", no_args_is_help=True, add_completion=False)

UrlOption = Annotated[str, typer.Option("--url", envvar="VECTOR_URL", help="Vector API base URL.")]
ApiKeyOption = Annotated[
    str | None,
    typer.Option("--api-key", envvar="VECTOR_API_KEY", help="Project API key (sent as X-Api-Key)."),
]
ProjectOption = Annotated[UUID, typer.Option("--project", help="Project id.")]


@dataclass(frozen=True)
class LocalFile:
    origin_key: str
    path: Path
    content_hash: str
    size: int


def _raise_walk_error(exc: OSError) -> NoReturn:
    raise exc


def _is_excluded(rel: str, excludes: Sequence[str]) -> bool:
    candidates = (rel, *rel.split("/"))
    return any(fnmatchcase(c, pattern) for pattern in excludes for c in candidates)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def scan_dir(
    root: Path, excludes: Sequence[str], max_bytes: int
) -> tuple[list[LocalFile], list[str]]:
    files: list[LocalFile] = []
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, onerror=_raise_walk_error
    ):
        current = Path(dirpath)
        for name in list(dirnames):
            if name.startswith("."):
                dirnames.remove(name)
            elif (current / name).is_symlink():
                dirnames.remove(name)
                skipped.append((current / name).relative_to(root).as_posix())
        dirnames.sort()
        for name in sorted(filenames):
            path = current / name
            rel = path.relative_to(root).as_posix()
            if name.startswith(".") or _is_excluded(rel, excludes):
                skipped.append(rel)
                continue
            extension = PurePosixPath(name).suffix.lower().lstrip(".")
            if extension not in SUPPORTED_EXTENSIONS:
                skipped.append(rel)
                continue
            status = path.lstat()
            if not S_ISREG(status.st_mode) or status.st_size > max_bytes:
                skipped.append(rel)
                continue
            files.append(LocalFile(rel, path, _sha256(path), status.st_size))
    return files, skipped


def make_client(url: str, api_key: str) -> VectorClient:
    return VectorClient(url, api_key)


def _require_api_key(api_key: str | None) -> str:
    if not api_key:
        typer.echo("error: --api-key or VECTOR_API_KEY is required", err=True)
        raise typer.Exit(2)
    return api_key


@contextmanager
def _api_errors() -> Iterator[None]:
    try:
        yield
    except VectorApiError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _upload(
    client: VectorClient,
    project: UUID,
    sync_root: str,
    rels: Sequence[str],
    local: dict[str, LocalFile],
) -> tuple[int, int]:
    uploaded = errors = 0
    for rel in rels:
        try:
            client.upload(project, sync_root, rel, local[rel].path)
        except (VectorApiError, OSError) as exc:
            errors += 1
            typer.echo(f"error: {rel}: {exc}", err=True)
        else:
            uploaded += 1
    return uploaded, errors


def _prune(
    client: VectorClient, project: UUID, sync_root: str, source_ids: Sequence[str]
) -> tuple[int, int]:
    pruned = errors = 0
    for source_id in source_ids:
        try:
            client.delete_synced(project, sync_root, source_id)
        except VectorApiError as exc:
            errors += 1
            typer.echo(f"error: prune {source_id}: {exc}", err=True)
        else:
            pruned += 1
    return pruned, errors


def _print_dry_run(plan: SyncPlanResult, skipped: Sequence[str], prune: bool) -> None:
    for rel in plan.to_upload:
        typer.echo(f"would upload {rel}")
    would_prune = plan.to_prune if prune else []
    for source_id in would_prune:
        typer.echo(f"would prune {source_id}")
    typer.echo(
        f"would_upload={len(plan.to_upload)} unchanged={len(plan.unchanged)} "
        f"would_prune={len(would_prune)} skipped={len(skipped)}"
    )


@app.command()
def sync(
    directory: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=False, dir_okay=True, readable=True),
    ],
    project: ProjectOption,
    url: UrlOption = DEFAULT_URL,
    api_key: ApiKeyOption = None,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune",
            help=(
                "Delete sources synced from this root that are no longer uploaded, "
                "including files now excluded, hidden, unsupported or skipped as too large."
            ),
        ),
    ] = False,
    exclude: Annotated[
        list[str] | None,
        typer.Option(
            "--exclude",
            help="fnmatch pattern for a relative path or any of its components; repeatable.",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan without uploads or deletes.")
    ] = False,
    sync_root: Annotated[
        str | None,
        typer.Option("--sync-root", help="Sync root id; defaults to the absolute directory path."),
    ] = None,
    max_bytes: Annotated[
        int, typer.Option("--max-bytes", min=1, help="Skip files larger than this many bytes.")
    ] = DEFAULT_MAX_BYTES,
) -> None:
    key = _require_api_key(api_key)
    root = directory.resolve()
    root_key = sync_root if sync_root is not None else root.as_posix()
    try:
        files, skipped = scan_dir(root, exclude or [], max_bytes)
    except OSError as exc:
        typer.echo(f"error: scan failed, nothing uploaded or pruned: {exc}", err=True)
        raise typer.Exit(1) from exc
    local = {f.origin_key: f for f in files}
    manifest = [SyncManifestEntry(f.origin_key, f.content_hash, f.size) for f in files]
    with make_client(url, key) as client, _api_errors():
        plan = client.sync_plan(project, root_key, manifest)
        if dry_run:
            _print_dry_run(plan, skipped, prune)
            return
        uploaded, errors = _upload(client, project, root_key, plan.to_upload, local)
        pruned = 0
        if prune and errors:
            typer.echo("error: prune skipped because of upload errors", err=True)
        elif prune:
            pruned, prune_errors = _prune(client, project, root_key, plan.to_prune)
            errors += prune_errors
    typer.echo(
        f"uploaded={uploaded} unchanged={len(plan.unchanged)} pruned={pruned} "
        f"skipped={len(skipped)} errors={errors}"
    )
    if errors:
        raise typer.Exit(1)


@app.command()
def reindex(
    project: ProjectOption,
    url: UrlOption = DEFAULT_URL,
    api_key: ApiKeyOption = None,
) -> None:
    key = _require_api_key(api_key)
    with make_client(url, key) as client, _api_errors():
        job = client.reindex(project)
    typer.echo(f"reindex job: {job['id']}")


@app.command()
def search(
    query: Annotated[str, typer.Argument(help="Search query.")],
    project: ProjectOption,
    url: UrlOption = DEFAULT_URL,
    api_key: ApiKeyOption = None,
    limit: Annotated[
        int, typer.Option("--limit", min=1, max=MAX_SEARCH_LIMIT, help="Maximum hits.")
    ] = DEFAULT_SEARCH_LIMIT,
) -> None:
    key = _require_api_key(api_key)
    with make_client(url, key) as client, _api_errors():
        result = client.search(project, query, limit)
    warning = result.get("warning")
    if warning:
        typer.echo(f"warning: {warning}", err=True)
    for hit in result["hits"]:
        typer.echo(
            f"[{hit['score']:.3f}] {hit['title']} (v{hit['version']}, source {hit['source_id']})"
        )
        typer.echo(f"  {hit['text']}")
