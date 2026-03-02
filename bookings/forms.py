from django import forms
from .models import Booking

class BookingForm(forms.ModelForm):
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