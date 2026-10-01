from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.shortcuts import render, redirect
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import BookingForm
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from .models import Booking, BookingAuditEvent, Room
from .models import BookingNotification
from .services.notifications import queue_booking_notification
from django.utils import timezone
from calendar import month_name, monthcalendar


def _is_htmx_request(request):
    return request.headers.get('HX-Request') == 'true'


def _pending_bookings_for(user):
    pending_bookings = Booking.objects.filter(status='Pending')
    if user.is_superuser:
        return pending_bookings
    return pending_bookings.filter(room__in=user.rooms_to_approve.all())


def _record_booking_event(booking, event_type, actor, detail=''):
    BookingAuditEvent.objects.create(
        booking=booking,
        event_type=event_type,
        actor=actor,
        detail=detail,
    )

# We use this decorator to ensure only logged-in Ministry staff can access this page
@login_required 
def book_room(request):
    if request.method == 'POST':
        # If the user clicked "Submit", we load the form with their data
        form = BookingForm(request.POST)
        
        if form.is_valid():
            # commit=False tells Django: "Hold on, don't save to the database just yet!"
            booking = form.save(commit=False) 
            
            # We automatically attach the currently logged-in officer to the booking
            booking.officer = request.user 
            
            try:
                booking.save()
            except IntegrityError:
                form.add_error('start_time', 'Sorry, this room was just booked during this time.')
                return render(request, 'bookings/book_room.html', {'form': form})
            _record_booking_event(booking, BookingAuditEvent.EventType.REQUESTED, request.user)

            queue_booking_notification(
                booking,
                BookingNotification.NotificationType.REQUESTED,
                booking.room.approvers.all(),
                request.build_absolute_uri(reverse('pending_approvals')),
            )
            
            messages.success(request, f"Successfully requested {booking.room.name}! Pending approval.")
            return redirect('book_room')
    else:
        initial_data = {}
        room_id = request.GET.get('room')
        
        if room_id:
            initial_data['room'] = room_id
        # If the user just navigated to the page, show an empty form
        form = BookingForm(initial=initial_data)

    return render(request, 'bookings/book_room.html', {'form': form})


@login_required
@require_GET
def booking_availability(request):
    """Returns live booking guidance without replacing final server validation."""
    required_fields = ('room', 'start_time', 'end_time')
    if not all(request.GET.get(field) for field in required_fields):
        return render(request, 'bookings/partials/availability_feedback.html')

    booking = None
    booking_id = request.GET.get('booking_id')
    if booking_id:
        booking = get_object_or_404(Booking, pk=booking_id, officer=request.user)

    form = BookingForm(request.GET, instance=booking)
    if form.is_valid():
        room = form.cleaned_data['room']
        features = []
        if room.has_projector:
            features.append('Projector')
        if room.has_video_conferencing:
            features.append('Video conferencing')
        return render(request, 'bookings/partials/availability_feedback.html', {
            'available': True,
            'room': room,
            'features': features,
        })

    errors = []
    for field_errors in form.errors.values():
        errors.extend(field_errors)
    return render(request, 'bookings/partials/availability_feedback.html', {
        'available': False,
        'errors': errors,
    })


def calendar_view(request):
    """Renders the HTML page containing the calendar."""
    return render(request, 'bookings/calendar.html')


def rooms(request):
    """Public room directory with current approved-booking availability."""
    now = timezone.now()
    rooms_with_status = []
    for room in Room.objects.filter(is_active=True).order_by('name'):
        current_booking = room.bookings.filter(
            status='Approved', start_time__lte=now, end_time__gte=now
        ).first()
        next_booking = room.bookings.filter(
            status='Approved', start_time__gt=now
        ).order_by('start_time').first()
        rooms_with_status.append({
            'room': room,
            'current_booking': current_booking,
            'next_booking': next_booking,
        })
    return render(request, 'bookings/rooms.html', {'rooms': rooms_with_status})


def api_bookings(request):
    """Outputs approved bookings for FullCalendar."""
    bookings = Booking.objects.filter(status='Approved')
    
    events = []
    for booking in bookings:
        events.append({
            'title': booking.purpose,
            'start': booking.start_time.isoformat(),
            'end': booking.end_time.isoformat(),
            'color': '#198754' if booking.status == 'Approved' else '#ffc107', # Green or Yellow
            'textColor': '#fff' if booking.status == 'Approved' else '#000',
            'extendedProps': {
                'purpose': booking.purpose,
                'room': booking.room.name,
            },
        })
    return JsonResponse(events, safe=False)


def dashboard(request):
    now = timezone.now()
    active_rooms = Room.objects.filter(is_active=True)
    room_data = []
    
    for room in active_rooms:
        # Pending requests are not published as room commitments.
        current_approved = room.bookings.filter(start_time__lte=now, end_time__gte=now, status='Approved').first() # type: ignore
        
        is_occupied = bool(current_approved)
        
        # Find the very next upcoming meeting
        next_booking = room.bookings.filter(start_time__gt=now, status='Approved').order_by('start_time').first() # type: ignore
        
        room_data.append({
            'room': room,
            'is_occupied': is_occupied,
            'current_booking': current_approved,
            'next_booking': next_booking
        })
        
    today = timezone.localdate()
    available_room_count = sum(not data['is_occupied'] for data in room_data)
    context = {
        'room_data': room_data,
        'current_time': now,
        'upcoming_bookings': Booking.objects.filter(
            status='Approved', start_time__gt=now
        ).select_related('room').order_by('start_time'),
        'total_room_count': active_rooms.count(),
        'today_booking_count': Booking.objects.filter(
            status='Approved', start_time__date=today
        ).count(),
        'available_room_count': available_room_count,
        'pending_booking_count': Booking.objects.filter(status='Pending').count(),
        'occupied_room_count': len(room_data) - available_room_count,
        'calendar_month_name': month_name[today.month],
        'calendar_year': today.year,
        'calendar_weeks': monthcalendar(today.year, today.month),
        'today_day': today.day,
    }
    return render(request, 'bookings/dashboard.html', context)


# View to handle the actual Approve/Reject button click
@login_required
def pending_approvals(request):
    # Find all rooms where the currently logged-in user is listed as an approver
    my_rooms = request.user.rooms_to_approve.all()
    
    # If they aren't assigned to any rooms (and aren't a superuser), kick them out
    if not my_rooms.exists() and not request.user.is_superuser:
        messages.error(request, "You are not designated as an approver for any rooms.")
        return redirect('dashboard')

    # Fetch pending bookings ONLY for the rooms this user controls
    pending_bookings = _pending_bookings_for(request.user).order_by('start_time')
        
    return render(request, 'bookings/pending_approvals.html', {'bookings': pending_bookings})


# bookings/views.py
from django.core.exceptions import ValidationError

@login_required
@require_POST
def process_booking(request, booking_id, action):
    booking = get_object_or_404(Booking, id=booking_id)
    
    # 1. SECURITY CHECK
    if request.user not in booking.room.approvers.all() and not request.user.is_superuser:
        messages.error(request, f"You are not assigned as an approver for {booking.room.name}.")
        return redirect('pending_approvals')
        
    if action not in {'approve', 'reject'}:
        messages.error(request, "Unknown booking action.")
        return redirect('pending_approvals')

    if booking.status != 'Pending':
        messages.error(request, "Only pending bookings can be approved or rejected.")
        return redirect('pending_approvals')

    rejection_reason = request.POST.get('reason', '').strip()
    if action == 'reject' and not rejection_reason:
        messages.error(request, "A rejection reason is required.")
        return redirect('pending_approvals')

    # 2. SET THE STATUS
    if action == 'approve':
        booking.status = 'Approved'
    elif action == 'reject':
        booking.status = 'Rejected'
        booking.rejection_reason = rejection_reason
    booking.decision_by = request.user
    booking.decided_at = timezone.now()

    # 3. TRY TO SAVE (Catch any double-booking errors!)
    try:
        booking.save()
        _record_booking_event(
            booking,
            BookingAuditEvent.EventType.APPROVED if action == 'approve' else BookingAuditEvent.EventType.REJECTED,
            request.user,
            rejection_reason,
        )
        
        # Success Messages
        if action == 'approve':
            messages.success(request, f"Booking for {booking.room.name} approved successfully.")
        else:
            messages.warning(request, f"Booking for {booking.room.name} has been rejected.")

        queue_booking_notification(
            booking,
            BookingNotification.NotificationType.APPROVED if action == 'approve' else BookingNotification.NotificationType.REJECTED,
            [booking.officer],
            request.build_absolute_uri(reverse('my_bookings')),
        )

        if _is_htmx_request(request):
            return render(request, 'bookings/partials/approval_update.html', {
                'booking': booking,
                'pending_count': _pending_bookings_for(request.user).count(),
            })

    except (IntegrityError, ValidationError) as e:
        # If the database rejects it (e.g., someone else was approved for this time slot first)
        if isinstance(e, IntegrityError):
            messages.error(request, "Cannot approve: this room was just booked during this time.")
        elif hasattr(e, 'message_dict'):
            for field, errors in e.message_dict.items():
                for error in errors:
                    messages.error(request, f"Cannot approve: {error}")
        else:
            for error in e.messages:
                messages.error(request, f"Cannot approve: {error}")

    return redirect('pending_approvals')


@login_required
def my_bookings(request):
    """Shows the logged-in officer all their past and future bookings."""
    # Fetch bookings for this specific user, ordered by newest first
    bookings = Booking.objects.filter(officer=request.user).order_by('-start_time')
    return render(request, 'bookings/my_bookings.html', {'bookings': bookings})


@login_required
def edit_booking(request, booking_id):
    """Allows an officer to edit their own booking."""
    # Ensure the user actually owns this booking
    booking = get_object_or_404(Booking, id=booking_id, officer=request.user)
    
    # Don't allow editing of rejected or cancelled bookings
    if booking.status in ['Rejected', 'Cancelled']:
        messages.error(request, "You cannot edit a rejected or cancelled booking.")
        return redirect('my_bookings')

    if request.method == 'POST':
        # Pass the existing booking 'instance' to the form so it knows we are updating, not creating
        form = BookingForm(request.POST, instance=booking)
        
        if form.is_valid():
            updated_booking = form.save(commit=False)
            
            # Since details changed, we put it back to Pending for the Secretary/IT to review
            updated_booking.status = 'Pending' 
            updated_booking.decision_by = None
            updated_booking.decided_at = None
            updated_booking.rejection_reason = ''
            try:
                updated_booking.save()
            except IntegrityError:
                form.add_error('start_time', 'Sorry, this room was just booked during this time.')
                return render(request, 'bookings/book_room.html', {'form': form, 'is_edit': True})
            _record_booking_event(updated_booking, BookingAuditEvent.EventType.UPDATED, request.user)
            
            queue_booking_notification(
                updated_booking,
                BookingNotification.NotificationType.UPDATED,
                updated_booking.room.approvers.all(),
                request.build_absolute_uri(reverse('pending_approvals')),
            )

            messages.success(request, "Booking updated successfully! It is now pending re-approval.")
            return redirect('my_bookings')
    else:
        # Pre-fill the form with the existing booking data
        form = BookingForm(instance=booking)

    # We can reuse our existing book_room.html template!
    # We pass 'is_edit': True so we can tweak the title on the page.
    return render(request, 'bookings/book_room.html', {'form': form, 'is_edit': True})


@login_required
@require_POST
def cancel_booking(request, booking_id):
    """Allows an officer to cancel their own booking."""
    # get_object_or_404 ensures they can only cancel THEIR OWN bookings
    booking = get_object_or_404(Booking, id=booking_id, officer=request.user)
    
    # You can only cancel meetings that haven't been rejected or already cancelled
    if booking.status in ['Pending', 'Approved']:
        booking.status = 'Cancelled'
        booking.cancelled_by = request.user
        booking.cancelled_at = timezone.now()
        booking.cancellation_reason = request.POST.get('reason', '').strip()
        booking.save()
        _record_booking_event(
            booking,
            BookingAuditEvent.EventType.CANCELLED,
            request.user,
            booking.cancellation_reason,
        )
        queue_booking_notification(
            booking,
            BookingNotification.NotificationType.CANCELLED,
            booking.room.approvers.all(),
            request.build_absolute_uri(reverse('pending_approvals')),
        )
        messages.success(request, f"Your booking for {booking.room.name} has been cancelled.")
        
        # Optional: You could add email logic here to notify the IT Team/Secretary 
        # that the room is now free again!
        
    return redirect('my_bookings')
