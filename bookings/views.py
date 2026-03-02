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
            
            # Send a success message to the template
            messages.success(request, f"Successfully requested {booking.room.name}!")
            return redirect('book_room') # Refresh the page to clear the form
    else:
        # If the user just navigated to the page, show an empty form
        form = BookingForm()

    return render(request, 'bookings/book_room.html', {'form': form})

# bookings/views.py

@login_required
def calendar_view(request):
    """Renders the HTML page containing the calendar."""
    return render(request, 'bookings/calendar.html')

@login_required
def api_bookings(request):
    """Outputs all active bookings as JSON for FullCalendar.js"""
    # We don't want to show cancelled or rejected meetings on the calendar
    bookings = Booking.objects.exclude(status__in=['Cancelled', 'Rejected'])
    
    events = []
    for booking in bookings:
        # FullCalendar expects specific keys like 'title', 'start', and 'end'
        events.append({
            'title': f"{booking.room.name} ({booking.status})",
            'start': booking.start_time.isoformat(),
            'end': booking.end_time.isoformat(),
            # Let's color-code based on status! 
            # Green for Approved, Yellow/Warning for Pending
            'color': '#198754' if booking.status == 'Approved' else '#ffc107',
            'textColor': '#fff' if booking.status == 'Approved' else '#000',
        })
        
    return JsonResponse(events, safe=False)


@login_required
def dashboard(request):
    """Displays the live status of all rooms."""
    now = timezone.now()
    active_rooms = Room.objects.filter(is_active=True)
    
    room_data = []
    
    for room in active_rooms:
        # Check if there is a booking happening RIGHT NOW for this room
        current_booking = room.bookings.filter(
            start_time__lte=now, # The meeting started in the past (or exactly now)
            end_time__gte=now,   # The meeting ends in the future (or exactly now)
            status__in=['Approved', 'Pending'] # Exclude cancelled/rejected
        ).first()
        
        room_data.append({
            'room': room,
            'is_occupied': bool(current_booking),
            'current_booking': current_booking
        })
        
    context = {
        'room_data': room_data,
        'current_time': now,
    }
    
    return render(request, 'bookings/dashboard.html', context)