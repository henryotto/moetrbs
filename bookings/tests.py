from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .models import Booking, BookingAuditEvent, BookingNotification, Room
from .services.notifications import queue_booking_notification


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class BookingWorkflowTests(TestCase):
    def setUp(self):
        self.requester = User.objects.create_user('requester', password='test-password')
        self.approver = User.objects.create_user('approver', password='test-password')
        self.room = Room.objects.create(name='ICT Conference Room', capacity=24, location='Main office')
        self.room.approvers.add(self.approver)
        self.start_time = timezone.localtime(timezone.now() + timedelta(days=1)).replace(
            minute=0, second=0, microsecond=0
        )
        self.end_time = self.start_time + timedelta(hours=1)

    def booking_payload(self, room=None):
        return {
            'room': (room or self.room).pk,
            'purpose': 'Planning workshop',
            'start_time': self.start_time.strftime('%Y-%m-%dT%H:%M'),
            'end_time': self.end_time.strftime('%Y-%m-%dT%H:%M'),
        }

    def create_pending_booking(self):
        return Booking.objects.create(
            room=self.room,
            officer=self.requester,
            purpose='Planning workshop',
            start_time=self.start_time,
            end_time=self.end_time,
        )

    def test_request_creates_exactly_one_booking(self):
        self.client.force_login(self.requester)

        response = self.client.post(reverse('book_room'), self.booking_payload())

        self.assertRedirects(response, reverse('book_room'))
        self.assertEqual(Booking.objects.count(), 1)
        booking = Booking.objects.get()
        self.assertEqual(booking.officer, self.requester)
        self.assertEqual(booking.status, 'Pending')
        self.assertTrue(BookingAuditEvent.objects.filter(
            booking=booking,
            event_type=BookingAuditEvent.EventType.REQUESTED,
            actor=self.requester,
        ).exists())

    def test_inactive_room_cannot_be_requested(self):
        inactive_room = Room.objects.create(
            name='Closed Room', capacity=8, location='Main office', is_active=False
        )
        self.client.force_login(self.requester)

        response = self.client.post(reverse('book_room'), self.booking_payload(inactive_room))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Booking.objects.count(), 0)
        self.assertContains(response, 'Select a valid choice')

    def test_overlapping_booking_is_rejected(self):
        self.create_pending_booking()

        with self.assertRaises(ValidationError):
            Booking.objects.create(
                room=self.room,
                officer=self.approver,
                purpose='Conflicting workshop',
                start_time=self.start_time,
                end_time=self.end_time,
            )

    def test_database_rejects_overlaps_when_model_validation_is_bypassed(self):
        self.create_pending_booking()
        conflicting_booking = Booking(
            room=self.room,
            officer=self.approver,
            purpose='Concurrent request',
            start_time=self.start_time,
            end_time=self.end_time,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Booking.objects.bulk_create([conflicting_booking])

    def test_database_allows_back_to_back_bookings(self):
        self.create_pending_booking()

        Booking.objects.bulk_create([Booking(
            room=self.room,
            officer=self.approver,
            purpose='Follow-up meeting',
            start_time=self.end_time,
            end_time=self.end_time + timedelta(hours=1),
        )])

        self.assertEqual(Booking.objects.count(), 2)

    def test_approval_requires_post_and_authorized_approver(self):
        booking = self.create_pending_booking()
        self.client.force_login(self.approver)
        url = reverse('process_booking', args=[booking.pk, 'approve'])

        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url)

        self.assertRedirects(response, reverse('pending_approvals'))
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'Approved')
        self.assertEqual(booking.decision_by, self.approver)
        self.assertIsNotNone(booking.decided_at)
        self.assertTrue(BookingAuditEvent.objects.filter(
            booking=booking,
            event_type=BookingAuditEvent.EventType.APPROVED,
            actor=self.approver,
        ).exists())

    def test_rejection_requires_a_reason_and_records_it(self):
        booking = self.create_pending_booking()
        self.client.force_login(self.approver)
        url = reverse('process_booking', args=[booking.pk, 'reject'])

        self.client.post(url)
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'Pending')

        self.client.post(url, {'reason': 'Room is required for an urgent meeting.'})
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'Rejected')
        self.assertEqual(booking.rejection_reason, 'Room is required for an urgent meeting.')
        self.assertEqual(booking.decision_by, self.approver)
        self.assertTrue(BookingAuditEvent.objects.filter(
            booking=booking,
            event_type=BookingAuditEvent.EventType.REJECTED,
            detail=booking.rejection_reason,
        ).exists())

    def test_htmx_approval_returns_partial_page_updates(self):
        booking = self.create_pending_booking()
        self.client.force_login(self.approver)

        response = self.client.post(
            reverse('process_booking', args=[booking.pk, 'approve']),
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="approval-feedback"')
        self.assertContains(response, 'id="approval-count"')
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'Approved')

    def test_pending_approvals_renders_progressive_enhancement_controls(self):
        self.create_pending_booking()
        self.client.force_login(self.approver)

        response = self.client.get(reverse('pending_approvals'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-bs-toggle="modal"')
        self.assertContains(response, 'hx-post=')

    def test_cancellation_requires_post_and_booking_owner(self):
        booking = self.create_pending_booking()
        self.client.force_login(self.requester)
        url = reverse('cancel_booking', args=[booking.pk])

        self.assertEqual(self.client.get(url).status_code, 405)
        response = self.client.post(url, {'reason': 'Meeting rescheduled.'})

        self.assertRedirects(response, reverse('my_bookings'))
        booking.refresh_from_db()
        self.assertEqual(booking.status, 'Cancelled')
        self.assertEqual(booking.cancelled_by, self.requester)
        self.assertEqual(booking.cancellation_reason, 'Meeting rescheduled.')
        self.assertTrue(BookingAuditEvent.objects.filter(
            booking=booking,
            event_type=BookingAuditEvent.EventType.CANCELLED,
        ).exists())

    def test_calendar_api_excludes_pending_bookings(self):
        pending_booking = self.create_pending_booking()
        approved_room = Room.objects.create(name='Training Room', capacity=20, location='Main office')
        Booking.objects.create(
            room=approved_room,
            officer=self.requester,
            purpose='Approved workshop',
            start_time=self.start_time + timedelta(hours=2),
            end_time=self.end_time + timedelta(hours=2),
            status='Approved',
        )
        response = self.client.get(reverse('api_bookings'))

        self.assertEqual(response.status_code, 200)
        titles = [event['title'] for event in response.json()]
        self.assertNotIn(pending_booking.purpose, titles)
        self.assertIn('Approved workshop', titles)

    def test_dashboard_and_calendar_are_public(self):
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.assertEqual(self.client.get(reverse('calendar')).status_code, 200)
        rooms_response = self.client.get(reverse('rooms'))
        self.assertEqual(rooms_response.status_code, 200)
        self.assertContains(rooms_response, self.room.name)
        self.assertEqual(self.client.get(reverse('api_bookings')).status_code, 200)

    def test_admin_can_upload_room_image_for_public_cards(self):
        administrator = User.objects.create_superuser('room-admin', 'admin@example.com', 'test-password')
        self.client.force_login(administrator)
        image_bytes = BytesIO()
        Image.new('RGB', (8, 8), '#007565').save(image_bytes, format='PNG')
        upload = SimpleUploadedFile('meeting-room.png', image_bytes.getvalue(), content_type='image/png')

        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse('admin:bookings_room_add'), {
                'name': 'Uploaded Room',
                'capacity': 12,
                'location': 'Main office',
                'image': upload,
                'image_url': 'https://example.com/old-photo.png',
                'is_active': 'on',
            })

            self.assertEqual(response.status_code, 302)
            room = Room.objects.get(name='Uploaded Room')
            self.assertTrue(room.image.name.startswith('rooms/'))
            self.assertEqual(room.display_image_url, room.image.url)
            self.assertContains(self.client.get(reverse('rooms')), room.image.url)
            self.assertContains(self.client.get(reverse('dashboard')), room.image.url)

    def test_booking_availability_returns_room_details(self):
        self.room.has_projector = True
        self.room.has_video_conferencing = True
        self.room.save()
        self.client.force_login(self.requester)

        response = self.client.get(reverse('booking_availability'), self.booking_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'is available')
        self.assertContains(response, 'Capacity: 24')
        self.assertContains(response, 'Projector')
        self.assertContains(response, 'Video conferencing')

    def test_booking_availability_reports_conflicts(self):
        self.create_pending_booking()
        self.client.force_login(self.requester)

        response = self.client.get(reverse('booking_availability'), self.booking_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already booked')

    def test_booking_emails_render_request_and_approval_details(self):
        self.approver.email = 'approver@example.com'
        self.approver.save(update_fields=['email'])
        self.requester.email = 'requester@example.com'
        self.requester.save(update_fields=['email'])
        booking = self.create_pending_booking()

        with self.captureOnCommitCallbacks(execute=True):
            queue_booking_notification(
                booking, BookingNotification.NotificationType.REQUESTED,
                [self.approver], 'https://example.com/approvals/',
            )

        self.assertEqual(len(mail.outbox), 1)
        request_email = mail.outbox[0]
        self.assertIn('New Room Booking Request', request_email.alternatives[0].content)
        self.assertIn('Review booking request', request_email.alternatives[0].content)
        self.assertIn('Planning workshop', request_email.body)
        self.assertIn('Content-ID: <moet-logo>', request_email.message().as_string())

        booking.status = 'Approved'
        booking.decision_by = self.approver
        booking.decided_at = timezone.now()
        booking.save()
        with self.captureOnCommitCallbacks(execute=True):
            queue_booking_notification(
                booking, BookingNotification.NotificationType.APPROVED,
                [self.requester], 'https://example.com/my-bookings/',
            )

        self.assertEqual(len(mail.outbox), 2)
        approved_email = mail.outbox[1]
        self.assertIn('Your Room Booking Has Been Approved', approved_email.alternatives[0].content)
        self.assertIn('Decided by: approver', approved_email.body)
        self.assertIn('https://example.com/my-bookings/', approved_email.body)
