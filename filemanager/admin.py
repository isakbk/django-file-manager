from django.contrib import admin
from .models import ActivityLog


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ("action", "item_name", "item_path", "created_at")
    list_filter = ("action", "created_at")
    search_fields = ("item_name", "item_path", "details")
    readonly_fields = (
        "action",
        "item_name",
        "item_path",
        "details",
        "created_at",
    )