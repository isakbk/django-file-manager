from django.urls import path
from . import views

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
    path("delete/",views.delete_item,name="delete_item"),
]