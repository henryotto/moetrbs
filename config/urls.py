from django.contrib import admin
from django.urls import path, include 

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('bookings.urls')), # Route all base traffic to your app
    path('accounts/', include('django.contrib.auth.urls')), # For built-in login/logout views
]