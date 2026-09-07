"""Smoke tests for the main views — verify they render without errors."""
import pytest
from django.test import Client
from django.urls import reverse
from django.contrib.auth.models import User
from kpi.models import UserProfile


@pytest.mark.django_db
class TestAuthViews:
    """Authentication page smoke tests."""

    def test_login_page_renders(self):
        client = Client()
        response = client.get(reverse("login"))
        assert response.status_code == 200

    def test_register_page_renders(self):
        client = Client()
        response = client.get(reverse("register"))
        assert response.status_code == 200

    def test_login_with_valid_credentials(self):
        user = User.objects.create_user(username="testuser", password="testpass123")
        UserProfile.objects.create(user=user, role="MANAGER")
        client = Client()
        response = client.post(reverse("login"), {
            "username": "testuser", "password": "testpass123"
        }, follow=True)
        # Should redirect to dashboard (or 2FA if enabled)
        assert response.status_code == 200

    def test_register_new_user(self):
        client = Client()
        response = client.post(reverse("register"), {
            "first_name": "Test",
            "last_name": "User",
            "username": "newuser",
            "email": "newuser@example.com",
            "password": "testpass123",
            "password2": "testpass123",
            "organization_name": "Test Hospital",
            "industry": "HOSPITAL",
            "role": "MANAGER",
        }, follow=True)
        assert response.status_code == 200
        assert User.objects.filter(username="newuser").exists()


@pytest.mark.django_db
class TestHealthCheck:
    """Health check endpoint."""

    def test_health_check(self):
        client = Client()
        response = client.get(reverse("health_check"))
        assert response.status_code in (200, 503)
        data = response.json()
        assert "status" in data


@pytest.mark.django_db
class TestDashboardRedirect:
    """Authenticated dashboard access."""

    def test_dashboard_requires_login(self):
        client = Client()
        response = client.get(reverse("dashboard"))
        assert response.status_code == 302  # redirect to login

    def test_dashboard_authenticated(self):
        user = User.objects.create_user(username="testuser", password="testpass123")
        UserProfile.objects.create(user=user, role="MANAGER")
        client = Client()
        client.login(username="testuser", password="testpass123")
        response = client.get(reverse("dashboard"))
        assert response.status_code == 200
