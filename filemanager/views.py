import mimetypes
import os
import shutil
import zipfile
import io
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import render, redirect
from django.utils.text import get_valid_filename
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required
from django.utils import timezone

from .models import ActivityLog


# =========================================================
# CONFIGURATION
# =========================================================

BASE_ROOT = Path(settings.FILE_MANAGER_ROOT).resolve()

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

def get_user_root(request):
    """Returns the isolated root directory for the current user."""
    if request.user.username == "admin" or request.user.is_superuser:
        user_root = BASE_ROOT
    else:
        user_root = BASE_ROOT / "users" / str(request.user.id)
    
    user_root.mkdir(parents=True, exist_ok=True)
    return user_root


def get_safe_path(request, relative_path=""):
    """Resolve a path while preventing access outside the user's root."""
    root = get_user_root(request)
    relative_path = (relative_path or "").replace("\\", "/").strip("/")
    candidate = (root / relative_path).resolve()

    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError("Invalid path.")

    return candidate


def relative_name(request, path):
    """Convert an absolute managed path to a relative path."""
    root = get_user_root(request)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return ""


def redirect_to_path(request, path):
    """Return to the directory the user was viewing."""
    return redirect("/?" + urlencode({"path": relative_name(request, path)}))


def log_activity(request, action, item, details="", description="", reason=""):
    """Save an activity record tied to the user."""
    ActivityLog.objects.create(
        user=request.user,
        action=action,
        item_name=Path(item).name or "Storage",
        item_path=str(item),
        details=details,
        description=description,
        reason=reason,
    )


def directory_is_safe(request, path):
    """Check that a path is inside managed storage and is not a symlink."""
    root = get_user_root(request)
    return (
        path.is_relative_to(root)
        and not path.is_symlink()
        and path.is_dir()
    )


# =========================================================
# HOME / FILE MANAGER
# =========================================================

@login_required
def home(request):
    try:
        current = get_safe_path(request, request.GET.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not directory_is_safe(request, current):
        raise Http404("Directory not found.")

    entries = []

    try:
        for item in current.iterdir():
            try:
                if item.is_symlink():
                    continue

                stat = item.stat()
                is_dir = item.is_dir()

                rel_path = relative_name(request, item)
                if not rel_path:
                    continue

                entries.append({
                    "name": item.name,
                    "path": rel_path,
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

    entries.sort(key=lambda item: (not item["is_dir"], item["name"].lower()))

    current_relative = relative_name(request, current)
    breadcrumbs = []
    accumulated = []

    if current_relative:
        for part in current_relative.split("/"):
            accumulated.append(part)
            breadcrumbs.append({
                "name": part,
                "path": "/".join(accumulated),
            })

    if request.user.username == "admin" or request.user.is_superuser:
        recent_activity = ActivityLog.objects.all()[:5]
    else:
        recent_activity = ActivityLog.objects.filter(user=request.user)[:5]

    context = {
        "items": entries,
        "entries": entries,
        "current_path": current_relative,
        "breadcrumbs": breadcrumbs,
        "total_files": sum(not item["is_dir"] for item in entries),
        "total_folders": sum(item["is_dir"] for item in entries),
        "recent_activity": recent_activity,
    }

    return render(request, "filemanager/index.html", context)


# =========================================================
# UPLOAD FILES AND FOLDERS
# =========================================================

@login_required
@require_POST
def upload_file(request):
    try:
        folder = get_safe_path(request, request.POST.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not directory_is_safe(request, folder):
        raise Http404("Directory not found.")

    uploaded_files = request.FILES.getlist("files")
    relative_paths = request.POST.getlist("relative_paths")
    description = request.POST.get("description", "").strip()
    reason = request.POST.get("reason", "").strip()

    if not uploaded_files:
        messages.error(request, "Please select files or a folder.")
        return redirect_to_path(request, folder)

    uploaded_count = 0
    skipped_count = 0
    created_folders = set()

    for index, uploaded in enumerate(uploaded_files):
        raw_path = (
            relative_paths[index]
            if index < len(relative_paths) and relative_paths[index]
            else uploaded.name
        )
        raw_path = raw_path.replace("\\", "/")

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

        base_relative = relative_name(request, folder)
        destination_relative = "/".join(
            ([base_relative] if base_relative else []) + safe_parts
        )

        try:
            destination = get_safe_path(request, destination_relative)

            if destination.exists() or destination.is_symlink():
                skipped_count += 1
                continue

            destination.parent.mkdir(parents=True, exist_ok=True)
            destination = get_safe_path(request, destination_relative)

            if (
                destination.exists()
                or destination.is_symlink()
                or not destination.parent.is_dir()
            ):
                skipped_count += 1
                continue

            with destination.open("xb") as output:
                for chunk in uploaded.chunks():
                    output.write(chunk)

            uploaded_count += 1

            parent = destination.parent
            while parent != folder and parent.is_relative_to(folder):
                parent_relative = relative_name(request, parent)

                if parent_relative not in created_folders:
                    log_activity(
                        request, "UPLOAD", parent_relative,
                        details=f"Uploaded folder: {parent.name}",
                        description=description, reason=reason,
                    )
                    created_folders.add(parent_relative)

                parent = parent.parent

            log_activity(
                request, "UPLOAD", relative_name(request, destination),
                details=f"Uploaded file: {destination.name}",
                description=description, reason=reason,
            )

        except (OSError, ValueError):
            skipped_count += 1

    if uploaded_count:
        messages.success(request, f"Successfully uploaded {uploaded_count} file(s).")

    if skipped_count:
        messages.warning(
            request,
            f"Skipped {skipped_count} file(s). "
            "Check file size, invalid paths, existing files, or permissions.",
        )

    return redirect_to_path(request, folder)


# =========================================================
# CREATE FOLDER
# =========================================================

@login_required
@require_POST
def create_folder(request):
    try:
        parent = get_safe_path(request, request.POST.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not directory_is_safe(request, parent):
        raise Http404("Directory not found.")

    raw_name = request.POST.get("folder_name", "").strip()
    name = get_valid_filename(raw_name)

    if not name or name in {".", ".."}:
        messages.error(request, "Enter a valid folder name.")
        return redirect_to_path(request, parent)

    target = parent / name

    if target.exists() or target.is_symlink():
        messages.error(request, "A file or folder with that name already exists.")
        return redirect_to_path(request, parent)

    try:
        target.mkdir()
        log_activity(
            request, "CREATE_FOLDER", relative_name(request, target),
            details=f"Created folder: {name}",
            description=request.POST.get("description", "").strip(),
            reason=request.POST.get("reason", "").strip(),
        )
        messages.success(request, f"Created folder: {name}.")
    except OSError:
        messages.error(request, "Could not create the folder.")

    return redirect_to_path(request, parent)


# =========================================================
# DOWNLOAD FILE & FOLDER
# =========================================================

@login_required
def download_file(request, path):
    try:
        target = get_safe_path(request, path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if target.is_symlink() or not target.is_file():
        raise Http404("File not found.")

    return FileResponse(
        target.open("rb"),
        as_attachment=True,
        filename=target.name,
    )


@login_required
def download_folder(request, path):
    """Zips a folder and downloads it."""
    try:
        folder = get_safe_path(request, path)
    except (ValueError, OSError):
        raise Http404("Invalid folder path.")

    if not folder.is_dir():
        raise Http404("Folder not found.")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(folder):
            if ".backups" in dirs:
                dirs.remove(".backups")
            for file in files:
                file_path = Path(root) / file
                arcname = file_path.relative_to(folder)
                zip_file.write(file_path, arcname)

    zip_buffer.seek(0)
    response = HttpResponse(zip_buffer, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{folder.name}.zip"'
    return response


# =========================================================
# OPEN / PREVIEW FILE
# =========================================================

@login_required
def preview_file(request, path):
    try:
        target = get_safe_path(request, path)
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

    response["Content-Disposition"] = f'inline; filename="{target.name}"'
    response["X-Content-Type-Options"] = "nosniff"

    return response


# =========================================================
# EDIT TEXT FILE / VIEW DOCUMENT
# =========================================================

@login_required
def edit_file(request, path):
    try:
        target = get_safe_path(request, path)
    except (ValueError, OSError):
        raise Http404("Invalid file path.")

    if target.is_symlink() or not target.is_file():
        raise Http404("File not found.")

    ext = target.suffix.lower()

    # PDF OR WORD: SHOW THE CUSTOM VIEWER
    if ext in {".pdf", ".docx", ".doc"}:
        return render(
            request,
            "filemanager/view_document.html",
            {
                "file_name": target.name,
                "file_path": relative_name(request, target),
            },
        )

    # OTHERWISE: STANDARD TEXT EDITOR
    editable = (
        ext in EDITABLE_EXTENSIONS
        or target.name == ".env.example"
    )

    if not editable:
        messages.error(request, "This file type cannot be edited as text.")
        return redirect_to_path(request, target.parent)

    try:
        if target.stat().st_size > MAX_EDIT_SIZE:
            messages.error(request, "Text editing is limited to files under 2 MB.")
            return redirect_to_path(request, target.parent)
    except OSError:
        raise Http404("Cannot access file.")

    if request.method == "POST":
        content = request.POST.get("content", "")

        if len(content.encode("utf-8")) > MAX_EDIT_SIZE:
            messages.error(request, "The edited file exceeds 2 MB.")
            return redirect(request.path)

        backup_dir = target.parent / ".backups"
        backup_dir.mkdir(exist_ok=True)
        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        backup_file = backup_dir / f"{target.name}.{timestamp}.bak"
        
        try:
            shutil.copy2(target, backup_file)
        except Exception:
            pass 

        temp = target.with_name(target.name + ".tmp")

        try:
            with temp.open("w", encoding="utf-8", newline="") as file:
                file.write(content)

            os.replace(temp, target)

            log_activity(
                request, "EDIT", relative_name(request, target),
                details=f"Edited file: {target.name}",
            )

            messages.success(request, "File saved successfully.")
            return redirect_to_path(request, target.parent)

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

    backups = []
    backup_dir = target.parent / ".backups"
    if backup_dir.exists():
        backups = sorted([b.name for b in backup_dir.glob(f"{target.name}.*.bak")], reverse=True)

    return render(
        request,
        "filemanager/edit.html",
        {
            "file_name": target.name,
            "file_path": relative_name(request, target),
            "content": content,
            "backups": backups,
        },
    )


# =========================================================
# RENAME FILE OR FOLDER
# =========================================================

@login_required
@require_POST
def rename_item(request):
    try:
        target = get_safe_path(request, request.POST.get("item_path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid path.")

    root = get_user_root(request)

    if not target.exists() or target.is_symlink() or target == root:
        raise Http404("Item not found.")

    new_name = get_valid_filename(request.POST.get("new_name", "").strip())

    if not new_name or new_name in {".", ".."}:
        messages.error(request, "Enter a valid new name.")
        return redirect_to_path(request, target.parent)

    destination = target.parent / new_name

    if destination.exists() or destination.is_symlink():
        messages.error(request, "That name already exists.")
        return redirect_to_path(request, target.parent)

    try:
        old_path = relative_name(request, target)
        target.rename(destination)

        log_activity(
            request, "RENAME", relative_name(request, destination),
            details=f"Previous path: {old_path}",
        )

        messages.success(request, "Renamed successfully.")
    except OSError:
        messages.error(request, "Could not rename the item.")

    return redirect_to_path(request, target.parent)


# =========================================================
# DELETE FILE OR FOLDER
# =========================================================

@login_required
@require_POST
def delete_item(request):
    try:
        target = get_safe_path(request, request.POST.get("item_path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid path.")

    root = get_user_root(request)

    if not target.exists() or target.is_symlink() or target == root:
        raise Http404("Item not found.")

    parent = target.parent
    deleted_path = relative_name(request, target)

    backup_dir = parent / ".backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")

    try:
        if target.is_dir():
            shutil.copytree(target, backup_dir / f"{target.name}.{timestamp}.bak")
        else:
            shutil.copy2(target, backup_dir / f"{target.name}.{timestamp}.bak")
    except Exception:
        pass

    try:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

        log_activity(request, "DELETE", deleted_path, details=f"Deleted: {deleted_path}")
        messages.success(request, "Item deleted. You can undo this.")
    except OSError:
        messages.error(request, "Could not delete the item. Check permissions.")

    return redirect_to_path(request, parent)


# =========================================================
# CREATE DOCUMENT & RESTORE BACKUP
# =========================================================

@login_required
@require_POST
def create_document(request):
    try:
        folder = get_safe_path(request, request.POST.get("path", ""))
    except (ValueError, OSError):
        raise Http404("Invalid directory.")

    if not directory_is_safe(request, folder):
        raise Http404("Directory not found.")

    raw_name = request.POST.get("file_name", "").strip()
    file_type = request.POST.get("file_type", "").strip()

    if not raw_name or not file_type:
        messages.error(request, "File name and type are required.")
        return redirect_to_path(request, folder)

    name = get_valid_filename(raw_name)
    if not name.endswith(f".{file_type}"):
        name = f"{name}.{file_type}"

    target = folder / name

    if target.exists():
        messages.error(request, "A file with that name already exists.")
        return redirect_to_path(request, folder)

    try:
        if file_type == "txt":
            target.write_text("", encoding="utf-8")
        elif file_type == "pdf":
            pdf_content = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\nxref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \ntrailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n110\n%%EOF"
            target.write_bytes(pdf_content)
        elif file_type == "docx":
            with zipfile.ZipFile(target, "w") as docx:
                docx.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
                docx.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
                docx.writestr("word/document.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body></w:body></w:document>')
        else:
            raise ValueError("Invalid file type.")

        log_activity(request, "CREATE_FILE", relative_name(request, target), details=f"Created document: {name}")
        messages.success(request, f"Created {name}.")

    except Exception as e:
        messages.error(request, f"Could not create document: {str(e)}")

    return redirect_to_path(request, folder)


@login_required
@require_POST
def restore_backup(request):
    backup_name = request.POST.get("backup_name")
    file_path = request.POST.get("file_path")

    try:
        target_dir = get_safe_path(request, file_path).parent
    except Exception:
        raise Http404("Invalid path.")

    backup_file = target_dir / ".backups" / backup_name
    if not backup_file.exists():
        messages.error(request, "Backup file not found.")
        return redirect("filemanager:home")

    original_name = backup_name.rsplit('.', 2)[0]
    original_target = target_dir / original_name

    try:
        if original_target.exists():
            timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(original_target, target_dir / ".backups" / f"{original_name}.{timestamp}.bak")

        if backup_file.is_dir():
            if original_target.exists(): shutil.rmtree(original_target)
            shutil.copytree(backup_file, original_target)
        else:
            shutil.copy2(backup_file, original_target)

        messages.success(request, f"Restored {original_name} successfully.")
    except Exception as e:
        messages.error(request, f"Failed to restore: {str(e)}")

    return redirect_to_path(request, target_dir)


# =========================================================
# ACTIVITY LOG PAGE
# =========================================================

@login_required
def activity(request):
    if request.user.username == "admin" or request.user.is_superuser:
        logs = ActivityLog.objects.all()[:500]
    else:
        logs = ActivityLog.objects.filter(user=request.user)[:500]

    return render(
        request,
        "filemanager/activity.html",
        {"logs": logs},
    )


# =========================================================
# DIRECTORY TREE
# =========================================================

def build_directory_tree(request, directory, depth=0):
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
                "path": relative_name(request, item),
                "is_dir": is_directory,
                "extension": item.suffix.lower(),
                "depth": depth,
                "children": [],
            }

            if is_directory:
                node["children"] = build_directory_tree(request, item, depth=depth + 1)

            nodes.append(node)

        except OSError:
            continue

    return nodes


@login_required
def directory_tree(request):
    tree = build_directory_tree(request, get_user_root(request))

    return render(
        request,
        "filemanager/tree.html",
        {
            "tree": tree,
            "current_path": request.GET.get("path", ""),
        },
    )

from django.http import JsonResponse

@login_required
def file_details(request):
    """API endpoint to fetch file details for the modal."""
    try:
        target = get_safe_path(request, request.GET.get("path", ""))
    except (ValueError, OSError):
        return JsonResponse({"error": "Invalid path"}, status=400)

    if not target.exists():
        return JsonResponse({"error": "File not found"}, status=404)

    stat = target.stat()
    is_dir = target.is_dir()
    
    # Get the latest activity log for this item to find Reason/Description
    log = ActivityLog.objects.filter(item_path=relative_name(request, target)).first()
    
    data = {
        "name": target.name,
        "type": "Folder" if is_dir else (target.suffix.upper().replace(".", "") or "File"),
        "size": "—" if is_dir else f"{stat.st_size / 1024:.1f} KB",
        "upload_date": timezone.localtime(log.created_at).strftime("%Y-%m-%d %H:%M") if log else "Unknown",
        "modify_date": timezone.localtime(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
        "reason": log.reason if log and log.reason else "No reason provided",
        "description": log.description if log and log.description else "No description provided",
        "path": relative_name(request, target)
    }
    return JsonResponse(data)