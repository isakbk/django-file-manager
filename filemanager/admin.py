from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import ActivityLog


# =========================================================
# ACTIVITY LOG ADMIN
# =========================================================


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


# =========================================================
# CUSTOM USER ADMIN (Admin can reset passwords)
# =========================================================

# Unregister the default User admin if it's already registered
try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    # This adds a dedicated password reset section in the admin panel
    fieldsets = UserAdmin.fieldsets + (
        (
            "Password Reset Info",
            {
                "fields": (),  # No fields, just informational
                "description": (
                    "To reset the user's password, use the 'Change password' link "
                    "at the top right corner of the user detail page. "
                    "Raw passwords are not displayed for security reasons."
                ),
            },
        ),
    )

    # Makes it easier to find users in the list
    list_display = (
        "username",
        "email",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
    )
    list_filter = ("is_staff", "is_superuser", "is_active")

    # Allows the admin to reset password directly from the user list view
    actions = ["reset_password_action"]

    @admin.action(description="Force password reset (requires manual entry)")
    def reset_password_action(self, request, queryset):
        # Django doesn't allow setting a password via bulk actions securely without raw text input,
        # so we redirect to the individual user page where the "Change password" link is.
        self.message_user(
            request,
            "Please click on individual users and use the 'Change password' link in the top right corner to reset their password.",
        )
