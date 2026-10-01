from django.utils import timezone
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.db import models
from django.db.models import Func, Q
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.templatetags.static import static


def validate_room_image_size(image):
    if image.size > 5 * 1024 * 1024:
        raise ValidationError('Room images must be 5 MB or smaller.')

class Room(models.Model):
    name = models.CharField(max_length=100, help_text="MOET ICT Conference Room")
    capacity = models.PositiveIntegerField()
    location = models.CharField(max_length=100, help_text="e.g., Curriculum Unit, Provincial Education")
    has_projector = models.BooleanField(default=False)
    has_video_conferencing = models.BooleanField(default=False)
    image = models.ImageField(
        upload_to='rooms/',
        blank=True,
        validators=[
            FileExtensionValidator(['jpg', 'jpeg', 'png', 'webp']),
            validate_room_image_size,
        ],
        help_text='Upload a JPG, PNG, or WebP image (up to 5 MB).',
    )
    image_url = models.URLField(
        blank=True,
        help_text='Optional external image URL, used only when no image is uploaded.',
    )
    is_active = models.BooleanField(default=True, help_text="Uncheck to disable booking for this room")
    
    approvers = models.ManyToManyField(
        User, 
        related_name='rooms_to_approve', 
        blank=True, 
        help_text="Select the specific officers/secretaries who can approve bookings for this room."
    )

    def __str__(self):
        return f"{self.name} (Capacity: {self.capacity})"

    @property
    def display_image_url(self):
        if self.image:
            return self.image.url
        return self.image_url or static('bookings/images/room-placeholder.svg')

class TsTzRange(Func):
    function = 'TSTZRANGE'
    output_field = DateTimeRangeField()


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
    decision_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booking_decisions',
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    cancelled_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='booking_cancellations',
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancellation_reason = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(start_time__lt=models.F('end_time')),
                name='booking_end_after_start',
            ),
            ExclusionConstraint(
                name='exclude_overlapping_active_room_bookings',
                expressions=[
                    (TsTzRange('start_time', 'end_time', RangeBoundary()), RangeOperators.OVERLAPS),
                    ('room', RangeOperators.EQUAL),
                ],
                condition=Q(status__in=['Pending', 'Approved']),
                violation_error_message='Sorry, this room is already booked during this time.',
            ),
        ]
    
    def clean(self):
        # 1. NEW LOGIC: Only prevent booking in the past for BRAND NEW bookings
        # 'not self.pk' means this booking hasn't been saved to the database yet.
        if not self.pk and self.start_time and self.start_time < timezone.now():
            raise ValidationError({'start_time': "You cannot book a room in the past."})

        # 2. Ensure the meeting doesn't end before it begins!
        if self.start_time and self.end_time and self.start_time >= self.end_time:
            raise ValidationError({'end_time': "The end time must be after the start time."})

        # 3. Prevent Double-Booking
        if self.start_time and self.end_time and self.room_id:
            overlapping_bookings = Booking.objects.filter(
                room_id=self.room_id,
                start_time__lt=self.end_time, 
                end_time__gt=self.start_time  
            ).exclude(status__in=['Cancelled', 'Rejected']) 

            if self.pk:
                overlapping_bookings = overlapping_bookings.exclude(pk=self.pk)

            if overlapping_bookings.exists():
                # Attach error specifically to the start_time field on the form
                raise ValidationError({'start_time': "Sorry, this room is already booked during this time."})

    def validate_constraints(self, exclude=None):
        """Keep PostgreSQL's conditional exclusion constraint database-enforced.

        Django's generic validation path cannot evaluate its condition against the
        synthetic exclusion-check query. ``clean()`` provides form feedback and
        PostgreSQL protects concurrent writes.
        """
        for constraint in self._meta.constraints:
            if not isinstance(constraint, ExclusionConstraint):
                constraint.validate(type(self), self, exclude=exclude)

    def save(self, *args, **kwargs):
        # Call clean() before saving to the database
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.room.name} booked by {self.officer.username}"


class BookingAuditEvent(models.Model):
    class EventType(models.TextChoices):
        REQUESTED = 'requested', 'Requested'
        UPDATED = 'updated', 'Updated'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        CANCELLED = 'cancelled', 'Cancelled'

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='audit_events')
    event_type = models.CharField(max_length=20, choices=EventType.choices)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    detail = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-id')

    def __str__(self):
        return f"{self.booking} {self.get_event_type_display().lower()}"


class BookingNotification(models.Model):
    class NotificationType(models.TextChoices):
        REQUESTED = 'requested', 'Request submitted'
        UPDATED = 'updated', 'Booking updated'
        APPROVED = 'approved', 'Booking approved'
        REJECTED = 'rejected', 'Booking rejected'
        CANCELLED = 'cancelled', 'Booking cancelled'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SENT = 'sent', 'Sent'
        FAILED = 'failed', 'Failed'

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NotificationType.choices)
    recipient = models.EmailField()
    recipient_name = models.CharField(max_length=150, blank=True)
    action_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at', '-id')
