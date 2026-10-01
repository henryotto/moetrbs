from email.mime.image import MIMEImage
from pathlib import Path

from django.core.mail import EmailMultiAlternatives
from django.contrib.staticfiles import finders
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from bookings.models import BookingNotification


def queue_booking_notification(booking, notification_type, recipients, action_url=''):
    notifications = []
    seen = set()
    for recipient in recipients:
        email = (recipient.email or '').strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        notification = BookingNotification.objects.create(
            booking=booking,
            notification_type=notification_type,
            recipient=email,
            recipient_name=recipient.get_full_name() or recipient.username,
            action_url=action_url,
        )
        notifications.append(notification)
        transaction.on_commit(lambda notification_id=notification.pk: deliver_booking_notification(notification_id))
    return notifications


def deliver_booking_notification(notification_id):
    notification = BookingNotification.objects.select_related(
        'booking__room', 'booking__officer'
    ).get(pk=notification_id)
    if notification.status == BookingNotification.Status.SENT:
        return
    context = {'notification': notification, 'booking': notification.booking}
    subject = f"Room Booking {notification.get_notification_type_display()}: {notification.booking.room.name}"
    text_body = render_to_string('bookings/emails/booking_notification.txt', context)
    html_body = render_to_string('bookings/emails/booking_notification.html', context)
    try:
        message = EmailMultiAlternatives(subject, text_body, to=[notification.recipient])
        message.attach_alternative(html_body, 'text/html')
        logo_path = finders.find('bookings/images/moet-logo-colour.png')
        if logo_path:
            logo = MIMEImage(Path(logo_path).read_bytes(), _subtype='png')
            logo.add_header('Content-ID', '<moet-logo>')
            logo.add_header('Content-Disposition', 'inline', filename='moet-logo.png')
            message.mixed_subtype = 'related'
            message.attach(logo)
        message.send(fail_silently=False)
    except Exception as exc:
        notification.status = BookingNotification.Status.FAILED
        notification.error_message = str(exc)[:2000]
        notification.save(update_fields=('status', 'error_message'))
    else:
        notification.status = BookingNotification.Status.SENT
        notification.sent_at = timezone.now()
        notification.error_message = ''
        notification.save(update_fields=('status', 'sent_at', 'error_message'))
