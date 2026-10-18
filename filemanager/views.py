import os
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseForbidden,
)
from django.shortcuts import render, redirect
from django.views.decorators.http import require_POST
from django.utils.text import get_valid_filename

from .models import ActivityLog


ROOT = Path(settings.FILE_MANAGER_ROOT).resolve()

# Only these file types can be edited as text.
EDITABLE_EXTENSIONS = {
    ".txt",
    ".py",
    ".html",
    ".htm",
    ".css",
    ".js",
    ".json",
    ".md",
    ".csv",
    ".xml",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
    ".sh",
    ".bat",
    ".log",
    ".env.example",
}

MAX_EDIT_SIZE = 2 * 1024 * 1024  # 2 MB
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50 MB


def get_safe_path(relative_path=""):
    """Resolve a path without allowing access outside managed_storage."""
    relative_path = (relative_path or "").replace("\\", "/").strip("/")

    candidate = (ROOT / relative_path).resolve()

    if candidate != ROOT and not candidate.is_relative_to(ROOT):
        raise ValueError("Invalid path.")

    return candidate


def relative_name(path):
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return ""


def log_activity(action, item, details="", description="", reason=""):
    ActivityLog.objects.create(
        action=action,
        item_name=Path(item).name or "Storage",
        item_path=str(item),
        details=details,
        description=description,
        reason=reason,
    )


def home(request):
    try:
        current = get_safe_path(request.GET.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not current.is_dir():
        raise Http404("Directory not found.")

    entries = []

    try:
        for item in current.iterdir():
            try:
                if item.is_symlink():
                    continue

                stat = item.stat()
                is_dir = item.is_dir()

                entries.append(
                    {
                        "name": item.name,
                        "path": relative_name(item),
                        "is_dir": is_dir,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "extension": item.suffix.lower(),
                    }
                )
            except OSError:
                continue
    except PermissionError:
        raise Http404("Cannot access this directory.")

    entries.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    relative = relative_name(current)
    breadcrumbs = []

    if relative:
        accumulated = []
        for part in relative.split("/"):
            accumulated.append(part)
            breadcrumbs.append(
                {
                    "name": part,
                    "path": "/".join(accumulated),
                }
            )

    total_files = sum(not item["is_dir"] for item in entries)
    total_folders = sum(item["is_dir"] for item in entries)

    context = {
        "entries": entries,
        "current_path": relative,
        "breadcrumbs": breadcrumbs,
        "total_files": total_files,
        "total_folders": total_folders,
        "recent_activity": ActivityLog.objects.all()[:5],
    }

    return render(request, "filemanager/index.html", context)


@require_POST
def upload_file(request):
    try:
        folder = get_safe_path(request.POST.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not folder.is_dir():
        raise Http404("Directory not found.")

    uploaded_files = request.FILES.getlist("files")

    if not uploaded_files:
        messages.error(request, "Please select a file.")
        return redirect(f"/?path={relative_name(folder)}")

    for uploaded in uploaded_files:
        name = get_valid_filename(Path(uploaded.name).name)

        if not name or name in {".", ".."}:
            messages.error(request, "Invalid file name.")
            continue

        if uploaded.size > MAX_UPLOAD_SIZE:
            messages.error(request, f"{name} exceeds the 50 MB limit.")
            continue

        destination = folder / name

        # Do not silently overwrite an existing file.
        if destination.exists():
            messages.error(request, f"{name} already exists.")
            continue

        try:
            with destination.open("xb") as output:
                for chunk in uploaded.chunks():
                    output.write(chunk)

            log_activity("UPLOAD", relative_name(destination))
            messages.success(request, f"Uploaded {name}")
        except OSError:
            messages.error(request, f"Could not upload {name}.")

    return redirect(f"/?path={relative_name(folder)}")


@require_POST
def create_folder(request):
    try:
        parent = get_safe_path(request.POST.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    name = get_valid_filename(request.POST.get("folder_name", "").strip())

    if not parent.is_dir():
        raise Http404("Directory not found.")

    if not name or name in {".", ".."}:
        messages.error(request, "Enter a valid folder name.")
        return redirect(f"/?path={relative_name(parent)}")

    target = parent / name

    if target.exists():
        messages.error(request, "A file or folder with that name already exists.")
    else:
        try:
            target.mkdir()
            log_activity("CREATE_FOLDER", relative_name(target))
            messages.success(request, f"Created folder {name}")
        except OSError:
            messages.error(request, "Could not create folder.")

    return redirect(f"/?path={relative_name(parent)}")


def download_file(request, path):
    try:
        target = get_safe_path(path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if not target.is_file() or target.is_symlink():
        raise Http404("File not found.")

    return FileResponse(
        target.open("rb"),
        as_attachment=True,
        filename=target.name,
    )


def preview_file(request, path):
    try:
        target = get_safe_path(path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if not target.is_file() or target.is_symlink():
        raise Http404("File not found.")

    if target.suffix.lower() != ".pdf":
        raise Http404("Only PDF preview is supported.")

    response = FileResponse(target.open("rb"), content_type="application/pdf")
    response["Content-Disposition"] = 'inline; filename="document.pdf"'
    response["X-Content-Type-Options"] = "nosniff"
    return response


def edit_file(request, path):
    try:
        target = get_safe_path(path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if not target.is_file() or target.is_symlink():
        raise Http404("File not found.")

    if target.suffix.lower() not in EDITABLE_EXTENSIONS:
        messages.error(
            request,
            "This file type cannot be edited as text. You can still download it.",
        )
        return redirect(f"/?path={relative_name(target.parent)}")

    if target.stat().st_size > MAX_EDIT_SIZE:
        messages.error(request, "Text editing is limited to files under 2 MB.")
        return redirect(f"/?path={relative_name(target.parent)}")

    if request.method == "POST":
        content = request.POST.get("content", "")

        if len(content.encode("utf-8")) > MAX_EDIT_SIZE:
            messages.error(request, "The edited file exceeds the 2 MB limit.")
            return redirect(request.path)

        try:
            # Write to a temporary file and replace the original.
            temp = target.with_name(target.name + ".tmp")

            with temp.open("w", encoding="utf-8", newline="") as file:
                file.write(content)

            os.replace(temp, target)

            log_activity("EDIT", relative_name(target))
            messages.success(request, "File saved successfully.")
            return redirect(f"/?path={relative_name(target.parent)}")

        except (OSError, UnicodeError):
            try:
                temp.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
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


@require_POST
def rename_item(request):
    try:
        target = get_safe_path(request.POST.get("item_path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid path.")

    new_name = get_valid_filename(request.POST.get("new_name", "").strip())

    if not target.exists() or target.is_symlink() or target == ROOT:
        raise Http404("Item not found.")

    if not new_name or new_name in {".", ".."}:
        messages.error(request, "Enter a valid new name.")
        return redirect(f"/?path={relative_name(target.parent)}")

    destination = target.parent / new_name

    if destination.exists():
        messages.error(request, "That name already exists.")
    else:
        try:
            old_path = relative_name(target)
            target.rename(destination)
            log_activity(
                "RENAME", relative_name(destination), f"Previous path: {old_path}"
            )
            messages.success(request, "Renamed successfully.")
        except OSError:
            messages.error(request, "Could not rename the item.")

    return redirect(f"/?path={relative_name(target.parent)}")


@require_POST
def delete_item(request):
    try:
        target = get_safe_path(request.POST.get("item_path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid path.")

    if not target.exists() or target.is_symlink() or target == ROOT:
        raise Http404("Item not found.")

    parent_path = relative_name(target.parent)

    try:
        # Only delete files or empty folders.
        if target.is_dir():
            target.rmdir()
        else:
            target.unlink()

        log_activity("DELETE", relative_name(target))
        messages.success(request, "Deleted successfully.")

    except OSError:
        messages.error(request, "Cannot delete this item. The folder may not be empty.")

    return redirect(f"/?path={parent_path}")


def activity(request):
    logs = ActivityLog.objects.all()[:500]
    return render(request, "filemanager/activity.html", {"logs": logs})


def build_directory_tree(directory):
    """Build a nested tree of folders and files inside managed storage."""
    nodes = []

    try:
        children = sorted(
            directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
        )
    except (PermissionError, OSError):
        return nodes

    for item in children:
        try:
            if item.is_symlink():
                continue

            relative_path = relative_name(item)

            node = {
                "name": item.name,
                "path": relative_path,
                "is_dir": item.is_dir(),
                "extension": item.suffix.lower(),
                "children": [],
            }

            if item.is_dir():
                node["children"] = build_directory_tree(item)

            nodes.append(node)

        except OSError:
            continue

    return nodes


def directory_tree(request):
    """Display all managed files and folders as a navigable tree."""
    tree = build_directory_tree(ROOT)

    return render(
        request,
        "filemanager/tree.html",
        {
            "tree": tree,
            "current_path": request.GET.get("path", ""),
        },
    )


def build_directory_tree(directory, depth=0):
    """Recursively list all files and folders at every nesting level."""
    nodes = []

    try:
        children = sorted(
            directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())
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
                node["children"] = build_directory_tree(item, depth=depth + 1)

            nodes.append(node)

        except OSError:
            continue

    return nodes
