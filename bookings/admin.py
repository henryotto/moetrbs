from django.contrib import admin
from django.utils.html import format_html
from .models import Room, Booking, BookingAuditEvent

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'capacity', 'location', 'is_active')
    fields = ('name', 'capacity', 'location', 'has_projector', 'has_video_conferencing', 'image', 'image_url', 'image_preview', 'is_active', 'approvers')
    readonly_fields = ('image_preview',)
    list_filter = ('is_active', 'has_projector', 'has_video_conferencing')

    @admin.display(description='Current image')
    def image_preview(self, obj):
        if not obj or not (obj.image or obj.image_url):
            return 'No image selected'
        return format_html(
            '<img src="{}" alt="Room image preview" style="max-width:240px;max-height:150px;object-fit:cover;border-radius:8px;">',
            obj.display_image_url,
        )

class BookingAuditEventInline(admin.TabularInline):
    model = BookingAuditEvent
    extra = 0
    can_delete = False
    readonly_fields = ('event_type', 'actor', 'detail', 'created_at')
    fields = readonly_fields


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('room', 'officer', 'start_time', 'end_time', 'status', 'decision_by', 'decided_at')
    list_filter = ('status', 'room', 'start_time')
    readonly_fields = ('created_at', 'decision_by', 'decided_at', 'cancelled_by', 'cancelled_at')
    inlines = (BookingAuditEventInline,)
