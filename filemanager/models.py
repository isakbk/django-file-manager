from django.db import models


class ActivityLog(models.Model):
    action = models.CharField(max_length=30)
    item_name = models.CharField(max_length=255)
    item_path = models.TextField(blank=True)
    description = models.TextField(blank=True)
    reason = models.TextField(blank=True)
    details = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.action}: {self.item_name}"
