"""
AI-Employee Platform — Load Test Suite (Locust)

Usage:
    # Interactive mode (Web UI at :8089)
    locust -f scripts/load_test.py --host http://localhost:8000

    # Headless mode (CI-friendly)
    locust -f scripts/load_test.py --host http://localhost:8000 \
        --headless -u 50 -r 5 --run-time 2m --csv results/load_test

    # Single-user stress test (for solo developers)
    locust -f scripts/load_test.py --host http://localhost:8000 \
        --headless -u 10 -r 2 --run-time 5m --csv results/single_user

Prerequisites:
    pip install locust
"""

import json
import random
import string

from locust import HttpUser, between, task


class APIUser(HttpUser):
    """Simulates a user interacting with the AI-Employee API."""

    wait_time = between(1, 3)
    access_token: str = ""
    agent_ids: list[str] = []

    def on_start(self):
        """Login or register before starting tests."""
        # Try login with test user
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"email": "loadtest@test.local", "password": "LoadTest123!"},
            name="/auth/login",
        )
        if resp.status_code == 200:
            self.access_token = resp.cookies.get("access_token", "")
            return

        # Register if login fails (first run)
        resp = self.client.post(
            "/api/v1/auth/register",
            json={
                "email": "loadtest@test.local",
                "password": "LoadTest123!",
                "name": "Load Test User",
            },
            name="/auth/register",
        )
        if resp.status_code in (200, 201):
            self.access_token = resp.cookies.get("access_token", "")

    # ── Health & Monitoring ──────────────────────────────────────

    @task(10)
    def health_check(self):
        """Health endpoint — should always be <100ms."""
        self.client.get("/health", name="/health")

    @task(5)
    def get_dashboard(self):
        """Dashboard metrics — moderate load."""
        self.client.get("/api/v1/health/dashboard", name="/health/dashboard")

    # ── Agent Listing ────────────────────────────────────────────

    @task(8)
    def list_agents(self):
        """List all agents — frequent operation."""
        resp = self.client.get("/api/v1/agents/", name="/agents (list)")
        if resp.status_code == 200:
            data = resp.json()
            agents = data if isinstance(data, list) else data.get("agents", [])
            self.agent_ids = [a["id"] for a in agents if "id" in a]

    @task(3)
    def get_agent_detail(self):
        """Get single agent detail."""
        if not self.agent_ids:
            return
        agent_id = random.choice(self.agent_ids)
        self.client.get(f"/api/v1/agents/{agent_id}", name="/agents/:id")

    # ── Task Listing ─────────────────────────────────────────────

    @task(6)
    def list_tasks(self):
        """List tasks — common dashboard operation."""
        self.client.get("/api/v1/tasks/", name="/tasks (list)")

    @task(2)
    def list_tasks_filtered(self):
        """List tasks with status filter."""
        status = random.choice(["completed", "failed", "running", "queued"])
        self.client.get(f"/api/v1/tasks/?status={status}", name="/tasks?status=*")

    # ── Notifications ────────────────────────────────────────────

    @task(4)
    def list_notifications(self):
        """Notification polling — happens frequently in UI."""
        self.client.get("/api/v1/notifications/", name="/notifications (list)")

    # ── Memory & Knowledge ───────────────────────────────────────

    @task(2)
    def search_knowledge(self):
        """Knowledge base search — tests embedding service."""
        queries = ["deployment process", "API documentation", "error handling", "testing"]
        self.client.get(
            f"/api/v1/knowledge/?q={random.choice(queries)}",
            name="/knowledge?q=*",
        )

    # ── Task Creation (Low frequency — expensive) ────────────────

    @task(1)
    def create_task(self):
        """Create a task — the most expensive operation (triggers agent work)."""
        if not self.agent_ids:
            return
        title = "Load test: " + "".join(random.choices(string.ascii_lowercase, k=8))
        resp = self.client.post(
            "/api/v1/tasks/",
            json={
                "title": title,
                "prompt": f"Echo 'load test {random.randint(1, 9999)}' — this is a test task.",
                "agent_id": random.choice(self.agent_ids),
                "priority": 1,
            },
            name="/tasks (create)",
        )
        # Task creation should succeed or fail gracefully
        if resp.status_code not in (200, 201, 400, 429):
            resp.failure(f"Unexpected status: {resp.status_code}")


class ReadOnlyUser(HttpUser):
    """Read-only user — simulates dashboard monitoring without writes."""

    wait_time = between(0.5, 2)
    weight = 3  # 3x more read-only users than full API users

    @task(10)
    def health(self):
        self.client.get("/health", name="/health")

    @task(5)
    def dashboard(self):
        self.client.get("/api/v1/health/dashboard", name="/health/dashboard")

    @task(8)
    def list_agents(self):
        self.client.get("/api/v1/agents/", name="/agents (list)")

    @task(6)
    def list_tasks(self):
        self.client.get("/api/v1/tasks/", name="/tasks (list)")

    @task(4)
    def notifications(self):
        self.client.get("/api/v1/notifications/", name="/notifications (list)")
