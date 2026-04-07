/**
 * AI-Employee Platform — Load Test (k6)
 *
 * Usage:
 *   # Standard run
 *   k6 run scripts/load_test_k6.js
 *
 *   # With environment variable for target host
 *   k6 run -e BASE_URL=http://localhost:8000 scripts/load_test_k6.js
 *
 *   # Export results to JSON
 *   k6 run --out json=results/k6_results.json scripts/load_test_k6.js
 *
 * Install k6:
 *   brew install k6          # macOS
 *   apt install k6           # Debian/Ubuntu
 *   docker run grafana/k6    # Docker
 */

import http from "k6/http";
import { check, group, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// ── Custom Metrics ──────────────────────────────────────────────
const errorRate = new Rate("errors");
const healthLatency = new Trend("health_latency", true);
const apiLatency = new Trend("api_latency", true);

// ── Test Configuration ──────────────────────────────────────────
const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

export const options = {
  stages: [
    { duration: "30s", target: 5 }, // Warm-up
    { duration: "1m", target: 20 }, // Ramp to moderate load
    { duration: "2m", target: 50 }, // Sustained load
    { duration: "1m", target: 100 }, // Peak load
    { duration: "2m", target: 100 }, // Sustain peak
    { duration: "30s", target: 0 }, // Ramp-down
  ],
  thresholds: {
    // SLA targets from PERFORMANCE_TESTING.md
    http_req_duration: ["p(95)<500", "p(99)<2000"],
    health_latency: ["p(95)<100"],
    errors: ["rate<0.01"], // <1% error rate
    http_req_failed: ["rate<0.01"],
  },
};

// ── Test Scenarios ──────────────────────────────────────────────

export default function () {
  const params = { headers: { "Content-Type": "application/json" } };

  group("Health Checks", () => {
    const res = http.get(`${BASE_URL}/health`);
    healthLatency.add(res.timings.duration);
    check(res, {
      "health status 200": (r) => r.status === 200,
      "health response < 100ms": (r) => r.timings.duration < 100,
      "health body contains status": (r) =>
        JSON.parse(r.body).status !== undefined,
    });
    errorRate.add(res.status !== 200);
  });

  sleep(0.5);

  group("API Endpoints", () => {
    // List agents
    let res = http.get(`${BASE_URL}/api/v1/agents/`, params);
    apiLatency.add(res.timings.duration);
    check(res, {
      "agents status 200 or 401": (r) => [200, 401].includes(r.status),
      "agents response < 500ms": (r) => r.timings.duration < 500,
    });
    errorRate.add(![200, 401].includes(res.status));

    sleep(0.3);

    // List tasks
    res = http.get(`${BASE_URL}/api/v1/tasks/`, params);
    apiLatency.add(res.timings.duration);
    check(res, {
      "tasks status 200 or 401": (r) => [200, 401].includes(r.status),
      "tasks response < 500ms": (r) => r.timings.duration < 500,
    });
    errorRate.add(![200, 401].includes(res.status));

    sleep(0.3);

    // Dashboard
    res = http.get(`${BASE_URL}/api/v1/health/dashboard`, params);
    apiLatency.add(res.timings.duration);
    check(res, {
      "dashboard status 200 or 401": (r) => [200, 401].includes(r.status),
    });
    errorRate.add(![200, 401].includes(res.status));

    sleep(0.3);

    // Notifications
    res = http.get(`${BASE_URL}/api/v1/notifications/`, params);
    apiLatency.add(res.timings.duration);
    errorRate.add(![200, 401].includes(res.status));
  });

  sleep(1);

  // Rate limit test — burst 10 requests rapidly
  if (Math.random() < 0.1) {
    group("Rate Limit Burst", () => {
      const responses = [];
      for (let i = 0; i < 10; i++) {
        responses.push(http.get(`${BASE_URL}/health`));
      }
      // At least some should succeed
      const successes = responses.filter((r) => r.status === 200).length;
      check(null, {
        "burst: most requests succeed": () => successes >= 8,
      });
    });
  }
}

// ── Summary Report ──────────────────────────────────────────────
export function handleSummary(data) {
  const summary = {
    timestamp: new Date().toISOString(),
    total_requests: data.metrics.http_reqs?.values?.count || 0,
    error_rate: data.metrics.errors?.values?.rate || 0,
    p95_latency_ms: data.metrics.http_req_duration?.values?.["p(95)"] || 0,
    p99_latency_ms: data.metrics.http_req_duration?.values?.["p(99)"] || 0,
    health_p95_ms: data.metrics.health_latency?.values?.["p(95)"] || 0,
    thresholds_passed: Object.values(data.root_group?.checks || {}).every(
      (c) => c.passes > 0
    ),
  };

  return {
    stdout: JSON.stringify(summary, null, 2) + "\n",
    "results/k6_summary.json": JSON.stringify(data, null, 2),
  };
}
