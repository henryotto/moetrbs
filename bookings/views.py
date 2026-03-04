from django.core.mail import send_mail
from django.shortcuts import get_object_or_404
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import BookingForm
from django.http import JsonResponse
from .models import Booking, Room
from django.utils import timezone

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
            
            # Now we save it. Our clean() method from Phase 2 will run automatically here.
            booking.save() 
            if form.is_valid():
                booking = form.save(commit=False) 
                booking.officer = request.user 
                booking.save() 
                
                # --- NEW: EMAIL NOTIFICATION TO SECRETARY ---
                subject = f"New Room Booking Request: {booking.room.name}"
                message = f"A new booking request has been submitted by {booking.officer.username} for {booking.room.name}.\n\nPlease log in to the system to approve or reject this request."
                
                # Grab the email addresses of everyone assigned to approve this specific room
                approver_emails = [user.email for user in booking.room.approvers.all() if user.email]
                
                # Only send the email if the room actually has approvers with email addresses set up
                if approver_emails:
                    send_mail(
                        subject,
                        message,
                        None, # Uses DEFAULT_FROM_EMAIL from settings
                        approver_emails, # Sends to the specific IT Officers or Secretaries
                        fail_silently=True,
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


def calendar_view(request):
    """Renders the HTML page containing the calendar."""
    return render(request, 'bookings/calendar.html')


def api_bookings(request):
    """Outputs Approved AND Pending bookings for FullCalendar"""
    bookings = Booking.objects.filter(status__in=['Approved', 'Pending']) 
    
    events = []
    for booking in bookings:
        events.append({
            'title': f"{booking.room.name} ({booking.status})",
            'start': booking.start_time.isoformat(),
            'end': booking.end_time.isoformat(),
            'color': '#198754' if booking.status == 'Approved' else '#ffc107', # Green or Yellow
            'textColor': '#fff' if booking.status == 'Approved' else '#000',
        })
    return JsonResponse(events, safe=False)


def dashboard(request):
    now = timezone.now()
    active_rooms = Room.objects.filter(is_active=True)
    room_data = []
    
    for room in active_rooms:
        # Check for both Approved and Pending bookings right now
        current_approved = room.bookings.filter(start_time__lte=now, end_time__gte=now, status='Approved').first() # type: ignore
        current_pending = room.bookings.filter(start_time__lte=now, end_time__gte=now, status='Pending').first() # type: ignore
        
        is_occupied = bool(current_approved)
        # It's only considered "Pending State" if it's not actually occupied by an approved meeting
        is_pending = bool(current_pending) and not is_occupied
        
        # Find the very next upcoming meeting
        next_booking = room.bookings.filter(start_time__gt=now, status__in=['Approved', 'Pending']).order_by('start_time').first() # type: ignore
        
        room_data.append({
            'room': room,
            'is_occupied': is_occupied,
            'is_pending': is_pending,
            'current_booking': current_approved or current_pending,
            'next_booking': next_booking
        })
        
    context = {'room_data': room_data, 'current_time': now}
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
    if request.user.is_superuser:
        # Superusers can see everything just in case
        pending_bookings = Booking.objects.filter(status='Pending').order_by('start_time')
    else:
        pending_bookings = Booking.objects.filter(status='Pending', room__in=my_rooms).order_by('start_time')
        
    return render(request, 'bookings/pending_approvals.html', {'bookings': pending_bookings})


# bookings/views.py
from django.core.exceptions import ValidationError

@login_required
def process_booking(request, booking_id, action):
    booking = get_object_or_404(Booking, id=booking_id)
    
    # 1. SECURITY CHECK
    if request.user not in booking.room.approvers.all() and not request.user.is_superuser:
        messages.error(request, f"You are not assigned as an approver for {booking.room.name}.")
        return redirect('pending_approvals')
        
    # 2. SET THE STATUS
    if action == 'approve':
        booking.status = 'Approved'
    elif action == 'reject':
        booking.status = 'Rejected'

    # 3. TRY TO SAVE (Catch any double-booking errors!)
    try:
        booking.save()
        
        # Success Messages
        if action == 'approve':
            messages.success(request, f"Booking for {booking.room.name} approved successfully.")
        else:
            messages.warning(request, f"Booking for {booking.room.name} has been rejected.")

        # --- EMAIL NOTIFICATION LOGIC ---
        subject = f"Room Booking {booking.status}: {booking.room.name}"
        message = f"Hello {booking.officer.first_name},\n\nYour request for {booking.room.name} on {booking.start_time.strftime('%Y-%m-%d %H:%M')} has been {booking.status}.\n\nThank you,\nMoET | Room Booking System"
        
        send_mail(
            subject, message, None, [booking.officer.email], fail_silently=True
        )

    except ValidationError as e:
        # If the database rejects it (e.g., someone else was approved for this time slot first)
        if hasattr(e, 'message_dict'):
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
            updated_booking.save()
            
            # --- NOTIFY APPROVERS OF THE CHANGE ---
            subject = f"UPDATED Room Booking: {updated_booking.room.name}"
            message = f"{updated_booking.officer.username} has updated their booking for {updated_booking.room.name}.\n\nPlease log in to review the new times."
            approver_emails = [user.email for user in updated_booking.room.approvers.all() if user.email]
            
            if approver_emails:
                send_mail(subject, message, None, approver_emails, fail_silently=True)

            messages.success(request, "Booking updated successfully! It is now pending re-approval.")
            return redirect('my_bookings')
    else:
        # Pre-fill the form with the existing booking data
        form = BookingForm(instance=booking)

    # We can reuse our existing book_room.html template!
    # We pass 'is_edit': True so we can tweak the title on the page.
    return render(request, 'bookings/book_room.html', {'form': form, 'is_edit': True})


@login_required
def cancel_booking(request, booking_id):
    """Allows an officer to cancel their own booking."""
    # get_object_or_404 ensures they can only cancel THEIR OWN bookings
    booking = get_object_or_404(Booking, id=booking_id, officer=request.user)
    
    # You can only cancel meetings that haven't been rejected or already cancelled
    if booking.status in ['Pending', 'Approved']:
        booking.status = 'Cancelled'
        booking.save()
        messages.success(request, f"Your booking for {booking.room.name} has been cancelled.")
        
        # Optional: You could add email logic here to notify the IT Team/Secretary 
        # that the room is now free again!
        
    return redirect('my_bookings')