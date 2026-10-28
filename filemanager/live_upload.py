
# filemanager/live_upload.py

from pathlib import Path

from django.conf import settings
from django.http import JsonResponse
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST

from .models import ActivityLog


ROOT = Path(settings.FILE_MANAGER_ROOT).resolve()
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB per file


def get_safe_path(relative_path=""):
    """Prevent paths from escaping the managed storage directory."""
    relative_path = (relative_path or "").replace("\\", "/").strip("/")
    target = (ROOT / relative_path).resolve()

    if target != ROOT and not target.is_relative_to(ROOT):
        raise ValueError("Invalid storage path.")

    return target


def relative_name(path):
    return path.relative_to(ROOT).as_posix()


def log_upload(item, description="", reason=""):
    ActivityLog.objects.create(
        action="UPLOAD",
        item_name=Path(item).name or "Storage",
        item_path=str(item),
        details=f"Uploaded: {Path(item).name}",
        description=description,
        reason=reason,
    )


@require_POST
def live_upload(request):
    """AJAX upload endpoint. Returns a JSON result for each file."""

    try:
        folder = get_safe_path(request.POST.get("path", ""))
    except (ValueError, OSError):
        return JsonResponse(
            {"success": False, "error": "Invalid destination path."},
            status=400,
        )

    if folder.is_symlink() or not folder.is_dir():
        return JsonResponse(
            {"success": False, "error": "Destination folder not found."},
            status=400,
        )

    uploaded_files = request.FILES.getlist("files")
    relative_paths = request.POST.getlist("relative_paths")
    description = request.POST.get("description", "").strip()
    reason = request.POST.get("reason", "").strip()

    if not uploaded_files:
        return JsonResponse(
            {"success": False, "error": "Select at least one file."},
            status=400,
        )

    results = []
    uploaded_count = 0
    failed_count = 0
    created_folders = set()

    for index, uploaded in enumerate(uploaded_files):
        raw_path = (
            relative_paths[index]
            if index < len(relative_paths) and relative_paths[index]
            else uploaded.name
        )
        raw_path = raw_path.replace("\\", "/")

        try:
            # Reject absolute paths, traversal and invalid components.
            if (
                raw_path.startswith("/")
                or "\x00" in raw_path
            ):
                raise ValueError("Invalid file path.")

            parts = raw_path.split("/")

            if (
                not parts
                or any(part in {"", ".", ".."} for part in parts)
                or ":" in parts[0]
            ):
                raise ValueError("Invalid file path.")

            safe_parts = [get_valid_filename(part) for part in parts]

            if any(
                not part or part in {".", ".."}
                for part in safe_parts
            ):
                raise ValueError("Invalid filename.")

            if uploaded.size > MAX_UPLOAD_SIZE:
                raise ValueError("File exceeds the 50 MB limit.")

            base_parts = (
                [relative_name(folder)] if folder != ROOT else []
            )
            destination_relative = "/".join(base_parts + safe_parts)

            # Validate before creating directories.
            destination = get_safe_path(destination_relative)

            if destination == ROOT:
                raise ValueError("Invalid filename.")

            destination.parent.mkdir(parents=True, exist_ok=True)

            # Resolve again after creating parent folders.
            destination = get_safe_path(destination_relative)

            if destination.is_symlink() or destination.exists():
                raise FileExistsError(
                    "A file or folder with this name already exists."
                )

            if not destination.parent.is_dir():
                raise ValueError("Destination folder is unavailable.")

            # Exclusive create mode prevents overwriting existing files.
            with destination.open("xb") as output:
                for chunk in uploaded.chunks():
                    output.write(chunk)

            uploaded_count += 1

            # Record newly created nested folders once per request.
            parent = destination.parent
            while parent != folder and parent.is_relative_to(folder):
                rel_parent = relative_name(parent)

                if rel_parent not in created_folders:
                    log_upload(
                        rel_parent,
                        description=description,
                        reason=reason,
                    )
                    created_folders.add(rel_parent)

                parent = parent.parent

            log_upload(
                relative_name(destination),
                description=description,
                reason=reason,
            )

            results.append({
                "filename": uploaded.name,
                "success": True,
                "message": "Upload completed successfully.",
                "size": uploaded.size,
            })

        except FileExistsError as exc:
            failed_count += 1
            results.append({
                "filename": uploaded.name,
                "success": False,
                "message": str(exc),
                "size": uploaded.size,
            })

        except (OSError, ValueError) as exc:
            failed_count += 1
            results.append({
                "filename": uploaded.name,
                "success": False,
                "message": str(exc) or "Upload failed.",
                "size": uploaded.size,
            })

    return JsonResponse({
        "success": failed_count == 0,
        "uploaded_count": uploaded_count,
        "failed_count": failed_count,
        "files": results,
    })
