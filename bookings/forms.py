from django import forms
from django.urls import reverse
from .models import Booking, Room

class BookingForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['room'].queryset = Room.objects.filter(is_active=True).order_by('name')
        for field_name in ('room', 'start_time', 'end_time'):
            self.fields[field_name].widget.attrs.update({
                'hx-get': reverse('booking_availability'),
                'hx-trigger': 'change',
                'hx-target': '#availability-feedback',
                'hx-include': '#booking-form',
                'hx-indicator': '#availability-spinner',
            })

    class Meta:
        model = Booking
        # We only ask the officer for these details. 
        # The 'officer' and 'status' will be handled securely by the backend.
        fields = ['room', 'purpose', 'start_time', 'end_time'] 
        
        # Adding Bootstrap classes for a clean Ministry-appropriate UI
        widgets = {
            'room': forms.Select(attrs={'class': 'form-select'}),
            'purpose': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'e.g., Annual Budget Review'}),
            # 'datetime-local' gives us a nice native calendar popup in the browser
            'start_time': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
            'end_time': forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
        }
