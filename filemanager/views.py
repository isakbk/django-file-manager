import mimetypes
import os
import shutil
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.shortcuts import render, redirect
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST

from .models import ActivityLog


# =========================================================
# CONFIGURATION
# =========================================================

ROOT = Path(settings.FILE_MANAGER_ROOT).resolve()

MAX_EDIT_SIZE = 2 * 1024 * 1024       # 2 MB
MAX_UPLOAD_SIZE = 50 * 1024 * 1024   # 50 MB

EDITABLE_EXTENSIONS = {
    ".txt", ".py", ".html", ".htm", ".css", ".js", ".json",
    ".md", ".csv", ".xml", ".yaml", ".yml", ".toml", ".ini",
    ".cfg", ".sql", ".sh", ".bat", ".log", ".example", ".ipynb",
}


# =========================================================
# PATH AND ACTIVITY HELPERS
# =========================================================

def get_safe_path(relative_path=""):
    """Resolve a path while preventing access outside ROOT."""
    relative_path = (relative_path or "").replace("\\", "/").strip("/")

    candidate = (ROOT / relative_path).resolve()

    if candidate != ROOT and not candidate.is_relative_to(ROOT):
        raise ValueError("Invalid path.")

    return candidate


def relative_name(path):
    """Convert an absolute managed path to a relative path."""
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return ""


def redirect_to_path(path):
    """Return to the directory the user was viewing."""
    return redirect("/?" + urlencode({"path": relative_name(path)}))


def log_activity(action, item, details="", description="", reason=""):
    """Save an activity record."""
    ActivityLog.objects.create(
        action=action,
        item_name=Path(item).name or "Storage",
        item_path=str(item),
        details=details,
        description=description,
        reason=reason,
    )


def directory_is_safe(path):
    """Check that a path is inside managed storage and is not a symlink."""
    return (
        path.is_relative_to(ROOT)
        and not path.is_symlink()
        and path.is_dir()
    )


# =========================================================
# HOME / FILE MANAGER
# =========================================================

def home(request):
    try:
        current = get_safe_path(request.GET.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not directory_is_safe(current):
        raise Http404("Directory not found.")

    entries = []

    try:
        for item in current.iterdir():
            try:
                if item.is_symlink():
                    continue

                stat = item.stat()
                is_dir = item.is_dir()

                entries.append({
                    "name": item.name,
                    "path": relative_name(item),
                    "is_dir": is_dir,
                    "size": stat.st_size,
                    "size_display": (
                        f"{stat.st_size / (1024 * 1024):.2f} MB"
                        if stat.st_size >= 1024 * 1024
                        else f"{stat.st_size / 1024:.1f} KB"
                    ),
                    "modified": stat.st_mtime,
                    "modified_at": stat.st_mtime,
                    "extension": item.suffix.lower(),
                })

            except OSError:
                continue

    except (PermissionError, OSError):
        raise Http404("Cannot access this directory.")

    # Folders first, then files, alphabetically.
    entries.sort(
        key=lambda item: (not item["is_dir"], item["name"].lower())
    )

    current_relative = relative_name(current)

    # Build breadcrumbs.
    breadcrumbs = []
    accumulated = []

    if current_relative:
        for part in current_relative.split("/"):
            accumulated.append(part)
            breadcrumbs.append({
                "name": part,
                "path": "/".join(accumulated),
            })

    context = {
        "items": entries,
        "entries": entries,
        "current_path": current_relative,
        "breadcrumbs": breadcrumbs,
        "total_files": sum(not item["is_dir"] for item in entries),
        "total_folders": sum(item["is_dir"] for item in entries),
        "recent_activity": ActivityLog.objects.all()[:5],
    }

    return render(request, "filemanager/index.html", context)


# =========================================================
# UPLOAD FILES AND FOLDERS
# =========================================================

@require_POST
def upload_file(request):
    try:
        folder = get_safe_path(request.POST.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not directory_is_safe(folder):
        raise Http404("Directory not found.")

    uploaded_files = request.FILES.getlist("files")
    relative_paths = request.POST.getlist("relative_paths")

    description = request.POST.get("description", "").strip()
    reason = request.POST.get("reason", "").strip()

    if not uploaded_files:
        messages.error(request, "Please select files or a folder.")
        return redirect_to_path(folder)

    uploaded_count = 0
    skipped_count = 0
    created_folders = set()

    for index, uploaded in enumerate(uploaded_files):

        # Use folder-relative paths when supplied by the template.
        raw_path = (
            relative_paths[index]
            if index < len(relative_paths) and relative_paths[index]
            else uploaded.name
        )

        raw_path = raw_path.replace("\\", "/")

        # Reject absolute paths and null bytes.
        if raw_path.startswith("/") or "\x00" in raw_path:
            skipped_count += 1
            continue

        parts = raw_path.split("/")

        if (
            not parts
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in parts[0]
        ):
            skipped_count += 1
            continue

        safe_parts = [get_valid_filename(part) for part in parts]

        if any(not part or part in {".", ".."} for part in safe_parts):
            skipped_count += 1
            continue

        if uploaded.size > MAX_UPLOAD_SIZE:
            skipped_count += 1
            continue

        base_relative = relative_name(folder)
        destination_relative = "/".join(
            ([base_relative] if base_relative else []) + safe_parts
        )

        try:
            destination = get_safe_path(destination_relative)

            # Do not overwrite existing files or follow a symlink.
            if destination.exists() or destination.is_symlink():
                skipped_count += 1
                continue

            # Create nested folders for folder uploads.
            destination.parent.mkdir(parents=True, exist_ok=True)

            # Resolve again after creating the parent directories.
            destination = get_safe_path(destination_relative)

            if (
                destination.exists()
                or destination.is_symlink()
                or not destination.parent.is_dir()
            ):
                skipped_count += 1
                continue

            # "xb" prevents overwriting an existing file.
            with destination.open("xb") as output:
                for chunk in uploaded.chunks():
                    output.write(chunk)

            uploaded_count += 1

            # Record newly created nested directories.
            parent = destination.parent

            while parent != folder and parent.is_relative_to(folder):
                parent_relative = relative_name(parent)

                if parent_relative not in created_folders:
                    log_activity(
                        "UPLOAD",
                        parent_relative,
                        details=f"Uploaded folder: {parent.name}",
                        description=description,
                        reason=reason,
                    )
                    created_folders.add(parent_relative)

                parent = parent.parent

            log_activity(
                "UPLOAD",
                relative_name(destination),
                details=f"Uploaded file: {destination.name}",
                description=description,
                reason=reason,
            )

        except (OSError, ValueError):
            skipped_count += 1

    if uploaded_count:
        messages.success(
            request,
            f"Successfully uploaded {uploaded_count} file(s).",
        )

    if skipped_count:
        messages.warning(
            request,
            f"Skipped {skipped_count} file(s). "
            "Check file size, invalid paths, existing files, or permissions.",
        )

    return redirect_to_path(folder)


# =========================================================
# CREATE FOLDER
# =========================================================

@require_POST
def create_folder(request):
    try:
        parent = get_safe_path(request.POST.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not directory_is_safe(parent):
        raise Http404("Directory not found.")

    raw_name = request.POST.get("folder_name", "").strip()
    name = get_valid_filename(raw_name)

    if not name or name in {".", ".."}:
        messages.error(request, "Enter a valid folder name.")
        return redirect_to_path(parent)

    target = parent / name

    if target.exists() or target.is_symlink():
        messages.error(
            request,
            "A file or folder with that name already exists.",
        )
        return redirect_to_path(parent)

    try:
        target.mkdir()

        log_activity(
            "CREATE_FOLDER",
            relative_name(target),
            details=f"Created folder: {name}",
            description=request.POST.get("description", "").strip(),
            reason=request.POST.get("reason", "").strip(),
        )

        messages.success(request, f"Created folder: {name}.")

    except OSError:
        messages.error(request, "Could not create the folder.")

    return redirect_to_path(parent)


# =========================================================
# DOWNLOAD FILE
# =========================================================

def download_file(request, path):
    try:
        target = get_safe_path(path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if target.is_symlink() or not target.is_file():
        raise Http404("File not found.")

    return FileResponse(
        target.open("rb"),
        as_attachment=True,
        filename=target.name,
    )


# =========================================================
# OPEN / PREVIEW FILE  (inline, any file type)
# =========================================================

def preview_file(request, path):
    """
    Serve ANY file inline so it opens in the browser instead of
    downloading. Works for text, JSON, notebooks, images, PDFs, etc.
    Unknown binary types fall back to application/octet-stream, which
    the browser will still display or download depending on the type.
    """
    try:
        target = get_safe_path(path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if target.is_symlink() or not target.is_file():
        raise Http404("File not found.")

    content_type, _ = mimetypes.guess_type(target.name)
    if not content_type:
        content_type = "application/octet-stream"

    response = FileResponse(
        target.open("rb"),
        content_type=content_type,
    )

    # Inline so the browser opens the file rather than downloading it.
    response["Content-Disposition"] = f'inline; filename="{target.name}"'
    response["X-Content-Type-Options"] = "nosniff"

    return response


# =========================================================
# EDIT TEXT FILE
# =========================================================

def edit_file(request, path):
    try:
        target = get_safe_path(path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if target.is_symlink() or not target.is_file():
        raise Http404("File not found.")

    editable = (
        target.suffix.lower() in EDITABLE_EXTENSIONS
        or target.name == ".env.example"
    )

    if not editable:
        messages.error(
            request,
            "This file type cannot be edited as text.",
        )
        return redirect_to_path(target.parent)

    try:
        if target.stat().st_size > MAX_EDIT_SIZE:
            messages.error(
                request,
                "Text editing is limited to files under 2 MB.",
            )
            return redirect_to_path(target.parent)
    except OSError:
        raise Http404("Cannot access file.")

    if request.method == "POST":
        content = request.POST.get("content", "")

        if len(content.encode("utf-8")) > MAX_EDIT_SIZE:
            messages.error(request, "The edited file exceeds 2 MB.")
            return redirect(request.path)

        temp = target.with_name(target.name + ".tmp")

        try:
            with temp.open("w", encoding="utf-8", newline="") as file:
                file.write(content)

            os.replace(temp, target)

            log_activity(
                "EDIT",
                relative_name(target),
                details=f"Edited file: {target.name}",
            )

            messages.success(request, "File saved successfully.")
            return redirect_to_path(target.parent)

        except (OSError, UnicodeError):
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

            messages.error(request, "Could not save the file.")

    try:
        content = target.read_text(encoding="utf-8")
    except (UnicodeError, OSError):
        raise Http404("This file is not valid UTF-8 text.")

    return render(
        request,
        "filemanager/edit.html",
        {
            "file_name": target.name,
            "file_path": relative_name(target),
            "content": content,
        },
    )


# =========================================================
# RENAME FILE OR FOLDER
# =========================================================

@require_POST
def rename_item(request):
    try:
        target = get_safe_path(request.POST.get("item_path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid path.")

    if (
        not target.exists()
        or target.is_symlink()
        or target == ROOT
    ):
        raise Http404("Item not found.")

    new_name = get_valid_filename(
        request.POST.get("new_name", "").strip()
    )

    if not new_name or new_name in {".", ".."}:
        messages.error(request, "Enter a valid new name.")
        return redirect_to_path(target.parent)

    destination = target.parent / new_name

    if destination.exists() or destination.is_symlink():
        messages.error(request, "That name already exists.")
        return redirect_to_path(target.parent)

    try:
        old_path = relative_name(target)
        target.rename(destination)

        log_activity(
            "RENAME",
            relative_name(destination),
            details=f"Previous path: {old_path}",
        )

        messages.success(request, "Renamed successfully.")

    except OSError:
        messages.error(request, "Could not rename the item.")

    return redirect_to_path(target.parent)


# =========================================================
# DELETE FILE OR FOLDER
# =========================================================

@require_POST
def delete_item(request):
    try:
        target = get_safe_path(request.POST.get("item_path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid path.")

    if (
        not target.exists()
        or target.is_symlink()
        or target == ROOT
    ):
        raise Http404("Item not found.")

    parent = target.parent
    deleted_path = relative_name(target)

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

        log_activity(
            "DELETE",
            deleted_path,
            details=f"Deleted: {deleted_path}",
        )

        messages.success(
            request,
            "File or folder deleted successfully.",
        )

    except OSError:
        messages.error(
            request,
            "Could not delete the item. Check permissions.",
        )

    return redirect_to_path(parent)


# =========================================================
# ACTIVITY LOG PAGE
# =========================================================

def activity(request):
    logs = ActivityLog.objects.all()[:500]

    return render(
        request,
        "filemanager/activity.html",
        {"logs": logs},
    )


# =========================================================
# DIRECTORY TREE
# =========================================================

def build_directory_tree(directory, depth=0):
    """Recursively return the managed files and folders."""
    nodes = []

    try:
        children = sorted(
            directory.iterdir(),
            key=lambda item: (
                not item.is_dir(),
                item.name.lower(),
            ),
        )
    except (PermissionError, OSError):
        return nodes

    for item in children:
        try:
            if item.is_symlink():
                continue

            is_directory = item.is_dir()

            node = {
                "name": item.name,
                "path": relative_name(item),
                "is_dir": is_directory,
                "extension": item.suffix.lower(),
                "depth": depth,
                "children": [],
            }

            if is_directory:
                node["children"] = build_directory_tree(
                    item,
                    depth=depth + 1,
                )

            nodes.append(node)

        except OSError:
            continue

    return nodes


def build_directory_tree_simple(directory):
    """Compatibility wrapper for older code."""
    return build_directory_tree(directory)


def directory_tree(request):
    tree = build_directory_tree(ROOT)

    return render(
        request,
        "filemanager/tree.html",
        {
            "tree": tree,
            "current_path": request.GET.get("path", ""),
        },
    )