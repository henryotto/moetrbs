from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'), # Homepage!
    path('book/', views.book_room, name='book_room'),
    path('calendar/', views.calendar_view, name='calendar'), # Calendar Page
    path('api/bookings/', views.api_bookings, name='api_bookings'), # The data
    path('approvals/', views.pending_approvals, name='pending_approvals'),
    path('process/<int:booking_id>/<str:action>/', views.process_booking, name='process_booking'),
    path('my-bookings/', views.my_bookings, name='my_bookings'),
    path('cancel/<int:booking_id>/', views.cancel_booking, name='cancel_booking'),
    path('edit/<int:booking_id>/', views.edit_booking, name='edit_booking'),
]