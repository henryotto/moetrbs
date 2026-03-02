from django.contrib import admin
from .models import Room, Booking

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'capacity', 'location', 'is_active')
    list_filter = ('is_active', 'has_projector', 'has_video_conferencing')

@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ('room', 'officer', 'start_time', 'end_time', 'status')
    list_filter = ('status', 'room', 'start_time')
