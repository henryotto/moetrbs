from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'), # Homepage!
    path('book/', views.book_room, name='book_room'),
    path('calendar/', views.calendar_view, name='calendar'), # Calendar Page
    path('api/bookings/', views.api_bookings, name='api_bookings'), # The data
]