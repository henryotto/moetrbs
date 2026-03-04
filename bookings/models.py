from django.utils import timezone
from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

class Room(models.Model):
    name = models.CharField(max_length=100, help_text="MOET ICT Conference Room")
    capacity = models.PositiveIntegerField()
    location = models.CharField(max_length=100, help_text="e.g., Curriculum Unit, Provincial Education")
    has_projector = models.BooleanField(default=False)
    has_video_conferencing = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True, help_text="Uncheck to disable booking for this room")
    
    approvers = models.ManyToManyField(
        User, 
        related_name='rooms_to_approve', 
        blank=True, 
        help_text="Select the specific officers/secretaries who can approve bookings for this room."
    )

    def __str__(self):
        return f"{self.name} (Capacity: {self.capacity})"

class Booking(models.Model):
    STATUS_CHOICES = (
        ('Pending', 'Pending'),
        ('Approved', 'Approved'),
        ('Rejected', 'Rejected'),
        ('Cancelled', 'Cancelled'),
    )

    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='bookings')
    officer = models.ForeignKey(User, on_delete=models.CASCADE)
    purpose = models.CharField(max_length=255)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    created_at = models.DateTimeField(auto_now_add=True)
    
    def clean(self):
        # 1. NEW LOGIC: Only prevent booking in the past for BRAND NEW bookings
        # 'not self.pk' means this booking hasn't been saved to the database yet.
        if not self.pk and self.start_time and self.start_time < timezone.now():
            raise ValidationError({'start_time': "You cannot book a room in the past."})

        # 2. Ensure the meeting doesn't end before it begins!
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({'end_time': "The end time must be after the start time."})

        # 3. Prevent Double-Booking
        if self.start_time and self.end_time and self.room:
            overlapping_bookings = Booking.objects.filter(
                room=self.room,
                start_time__lt=self.end_time, 
                end_time__gt=self.start_time  
            ).exclude(status__in=['Cancelled', 'Rejected']) 

            if self.pk:
                overlapping_bookings = overlapping_bookings.exclude(pk=self.pk)

            if overlapping_bookings.exists():
                # Attach error specifically to the start_time field on the form
                raise ValidationError({'start_time': f"Sorry, {self.room.name} is already booked during this time."})

    def save(self, *args, **kwargs):
        # Call clean() before saving to the database
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.room.name} booked by {self.officer.username}"
