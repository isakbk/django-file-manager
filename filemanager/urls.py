from django.urls import path
from . import views
from . import authenticate

app_name = "filemanager"

urlpatterns = [
    path("", views.home, name="home"),
    path("upload/", views.upload_file, name="upload"),
    path("folder/create/", views.create_folder, name="create_folder"),
    path("download/<path:path>/", views.download_file, name="download"),
    path("preview/<path:path>/", views.preview_file, name="preview"),
    path("edit/<path:path>/", views.edit_file, name="edit"),
    path("rename/", views.rename_item, name="rename"),
    path("delete/", views.delete_item, name="delete"),
    path("activity/", views.activity, name="activity"),
    path("tree/", views.directory_tree, name="tree"),
    path("delete/", views.delete_item, name="delete_item"),
    path("login/", authenticate.login_view, name="login"),
    path("register/", authenticate.register_view, name="register"),
    path("logout/", authenticate.logout_view, name="logout"),
    path("change-password/", authenticate.change_password_view, name="change_password"),
    path("download-folder/<path:path>/", views.download_folder, name="download_folder"),
    path("create-document/", views.create_document, name="create_document"),
    path("restore-backup/", views.restore_backup, name="restore_backup"),
    path("file-details/", views.file_details, name="file_details"),
]
