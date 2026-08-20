"""
Day 06 — chaos_workload.py

A workload that fails in an interesting way, on demand.

Monitoring pointed at nothing teaches nothing. Every observability tutorial
that ends at "and here is your dashboard" is skipping the only part that
matters, which is what the dashboard looks like at 03:00 when something is
actually wrong and you have four minutes to form an opinion.

So this function manufactures an incident. Invoke it and it writes a realistic
cascade of log lines into the workload log group -- the same log group the
metric filters in main.tf section 4 are attached to, and the same one the
analyser in section 10 will later read.

WHAT THE CASCADE IS

The story it tells is the most common production incident there is, and the
one people misdiagnose most reliably:

    1. A deployment lands. Among its config changes, one line reduces the
       database connection pool from 50 to 5. Nobody notices; it is one line
       in a diff of forty.
    2. For about ninety seconds nothing happens, because traffic is light and
       five connections is enough.
    3. Traffic reaches normal levels. Requests begin queueing for a connection.
       Latency climbs -- not errors yet, just slow.
    4. Connection acquisition starts timing out. Now there are errors.
    5. The client retries. Retries are new requests. They also need
       connections. Load doubles against a pool that was already exhausted.
       This is the amplification step and it is why the graph goes vertical
       rather than sloping.
    6. The circuit breaker opens and starts failing fast, which looks like
       recovery on a latency graph and is not.
    7. Upstream callers start returning 503 to customers.

THE POINT OF THE DESIGN

The cause -- the pool change -- appears exactly ONCE, at the very beginning,
as a single INFO line. Everything after it is consequence: thousands of ERROR
lines all describing the database, none of them mentioning the deploy.

That asymmetry is deliberate and it is the whole day.

A human reading these logs top to bottom finds the deploy line in about forty
seconds. An LLM handed the LAST two hundred lines -- which is the obvious way
to build a log summariser, and the way most people build it first -- never
sees the cause at all, and produces a fluent, confident, well-structured
summary that blames the database. It will suggest increasing the database
instance size. It will sound completely sure.

That summary is worse than no summary, because it is actionable and wrong, and
because a tired engineer at 03:00 will act on it.

trainer-notes.md turns that into a five-minute live demo. It is the most
valuable five minutes of the day.

INVOCATION

    {}                             -> cascade, 400 lines, over 12 minutes
    {"mode": "cascade"}            -> the full incident (default)
    {"mode": "normal"}             -> healthy traffic only, no errors
    {"mode": "latency"}            -> slow but successful; no error lines at
                                      all, which is how you learn that an
                                      error-rate alarm alone is not enough
    {"lines": 1200}                -> override the burst size
    {"window_minutes": 30}         -> spread the events over a longer window
    {"include_cause": false}       -> emit the cascade WITHOUT the deploy line.
                                      Use this to show that a summariser which
                                      cannot say "insufficient evidence" will
                                      invent a cause rather than admit it has
                                      none.

LOG FORMAT

One JSON object per line. JSON, not free text, because the metric filters in
section 4 use JSON selector patterns ({ $.level = "ERROR" }) and because
extracting a numeric VALUE -- latency_ms -- is what makes a p95 alarm possible.
Section 4's comments cover the text-pattern alternative and when it is right.

Fields are deliberately boring and stable: event, level, ts, service,
request_id, latency_ms, status, error_type, message. High-cardinality values
(request_id) are in the log line but are NEVER promoted to a metric dimension.
Section 4 explains what happens to your bill when they are.
"""

import json
import os
import random
import time
import uuid

import boto3

LOG_GROUP = os.environ["WORKLOAD_LOG_GROUP"]
DEFAULT_LINES = int(os.environ.get("DEFAULT_BURST_LINES", "400"))
SERVICE = os.environ.get("SERVICE_NAME", "checkout-api")

logs = boto3.client("logs")

# Bounded on purpose. Every distinct value here becomes a CloudWatch metric
# dimension value, and every distinct dimension value is a separate custom
# metric at $0.30/month that you cannot delete. Four is a set. request_id
# would be a bill.
ERROR_TYPES = ("DB_CONN_TIMEOUT", "POOL_EXHAUSTED", "CIRCUIT_OPEN", "UPSTREAM_5XX")

ENDPOINTS = ("/v1/checkout", "/v1/cart", "/v1/orders", "/health")


def _line(ts_ms, level, event, **fields):
    """One structured log record. ts is ISO-8601 for humans, ts_ms drives the
    CloudWatch event timestamp, which is what metric filters key off."""
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts_ms / 1000.0)),
        "level": level,
        "event": event,
        "service": SERVICE,
    }
    record.update(fields)
    return {"timestamp": int(ts_ms), "message": json.dumps(record, separators=(",", ":"))}


def _request(ts_ms, latency_ms, status, error_type=None, message=None):
    fields = {
        "request_id": str(uuid.uuid4()),
        "endpoint": random.choice(ENDPOINTS),
        "latency_ms": int(latency_ms),
        "status": int(status),
    }
    if error_type:
        fields["error_type"] = error_type
    if message:
        fields["message"] = message
    level = "ERROR" if status >= 500 else ("WARN" if latency_ms > 1500 else "INFO")
    return _line(ts_ms, level, "request_completed", **fields)


def build_cascade(start_ms, end_ms, total_lines, include_cause=True):
    """Build the seven-phase incident described in the module docstring.

    Phases are proportions of the window rather than fixed counts so that
    --lines and --window-minutes both stay meaningful.
    """
    span = max(end_ms - start_ms, 1000)
    events = []

    def at(fraction):
        return start_ms + int(span * fraction)

    # ---------------------------------------------------------------- phase 0
    # The cause. ONE line. This is the line the whole day is about.
    if include_cause:
        events.append(
            _line(
                at(0.01),
                "INFO",
                "config_applied",
                deploy_id="d-8f21ac",
                message=(
                    "applied deployment d-8f21ac: 3 config changes "
                    "(feature.newCheckout true->true, log.level INFO->INFO, "
                    "db.pool.maxConnections 50->5)"
                ),
            )
        )
        events.append(
            _line(
                at(0.012),
                "INFO",
                "deploy_completed",
                deploy_id="d-8f21ac",
                message="rollout complete, 6/6 tasks healthy",
            )
        )

    # Proportional budgets. They sum to 1.0.
    budget = {
        "calm": 0.18,
        "slow": 0.14,
        "errors": 0.20,
        "retry_storm": 0.26,
        "breaker": 0.14,
        "upstream": 0.08,
    }
    counts = {k: max(1, int(total_lines * v)) for k, v in budget.items()}

    # ---------------------------------------------------------------- phase 1
    # Calm. Healthy traffic. Five connections is enough at this rate, so
    # nothing looks wrong yet. This phase exists so the alarm has a baseline
    # and so the "before" half of the dashboard is not empty.
    for i in range(counts["calm"]):
        f = 0.02 + (0.18 * i / counts["calm"])
        events.append(_request(at(f), random.gauss(140, 35), 200))

    # ---------------------------------------------------------------- phase 2
    # Slow. Requests are queueing for a connection. NOTHING FAILS YET.
    #
    # This phase is why an error-rate alarm on its own is insufficient and why
    # section 5 builds a latency alarm too. For roughly a minute and a half the
    # service is unusable and the error rate is zero.
    for i in range(counts["slow"]):
        f = 0.20 + (0.14 * i / counts["slow"])
        latency = random.gauss(900 + 4000 * (i / counts["slow"]), 250)
        events.append(_request(at(f), max(latency, 200), 200))
    events.append(
        _line(
            at(0.28),
            "WARN",
            "pool_pressure",
            message="connection pool at capacity, 5/5 in use, 34 waiters queued",
            pool_size=5,
            waiters=34,
        )
    )

    # ---------------------------------------------------------------- phase 3
    # First errors. Acquisition times out.
    for i in range(counts["errors"]):
        f = 0.34 + (0.20 * i / counts["errors"])
        if random.random() < 0.55:
            events.append(
                _request(
                    at(f),
                    random.gauss(5100, 400),
                    503,
                    "DB_CONN_TIMEOUT",
                    "timed out after 5000ms waiting for a connection from pool 'checkout-db'",
                )
            )
        else:
            events.append(_request(at(f), random.gauss(4200, 600), 200))

    # ---------------------------------------------------------------- phase 4
    # The retry storm. This is the amplification step: the client's retry
    # policy turns one failed request into three, all of which also need a
    # connection from the same exhausted pool.
    #
    # If you only ever remember one thing about cascades, remember that the
    # vertical part of the graph is almost always retries, and that "add
    # capacity" is the wrong instinct while a retry storm is running.
    for i in range(counts["retry_storm"]):
        f = 0.54 + (0.26 * i / counts["retry_storm"])
        events.append(
            _request(
                at(f),
                random.gauss(5300, 500),
                503,
                random.choice(("DB_CONN_TIMEOUT", "POOL_EXHAUSTED")),
                "acquire failed: pool 'checkout-db' exhausted (max=5, active=5, queued=112); attempt 2 of 3",
            )
        )

    # ---------------------------------------------------------------- phase 5
    # Circuit breaker opens. Failures become FAST failures.
    #
    # Watch what this does to the latency graph: it drops, sharply, back to
    # near-normal. On a latency-only dashboard this looks exactly like
    # recovery. It is the opposite of recovery. Anyone who has trusted a
    # latency graph through a breaker transition has learned this once.
    events.append(
        _line(
            at(0.80),
            "ERROR",
            "circuit_opened",
            error_type="CIRCUIT_OPEN",
            message="circuit breaker 'checkout-db' OPEN after 20 consecutive failures; failing fast for 30s",
        )
    )
    for i in range(counts["breaker"]):
        f = 0.80 + (0.14 * i / counts["breaker"])
        events.append(
            _request(
                at(f),
                random.gauss(12, 4),
                503,
                "CIRCUIT_OPEN",
                "circuit breaker open, request rejected without attempting downstream call",
            )
        )

    # ---------------------------------------------------------------- phase 6
    # Customers see it.
    for i in range(counts["upstream"]):
        f = 0.94 + (0.05 * i / counts["upstream"])
        events.append(
            _request(
                at(f),
                random.gauss(45, 15),
                503,
                "UPSTREAM_5XX",
                "downstream checkout-api returned 503; returning 503 to client",
            )
        )

    return events


def build_normal(start_ms, end_ms, total_lines):
    span = max(end_ms - start_ms, 1000)
    return [
        _request(start_ms + int(span * i / total_lines), max(random.gauss(150, 40), 20), 200)
        for i in range(total_lines)
    ]


def build_latency_only(start_ms, end_ms, total_lines):
    """Slow, and entirely successful. No ERROR lines anywhere.

    Run this one and watch the error-rate alarm stay stubbornly OK while every
    customer waits four seconds. It is the cleanest demonstration in the lab
    that 'is it erroring' and 'is it working' are different questions.
    """
    span = max(end_ms - start_ms, 1000)
    return [
        _request(start_ms + int(span * i / total_lines), max(random.gauss(4200, 700), 500), 200)
        for i in range(total_lines)
    ]


def put_events(stream_name, events):
    """Write to CloudWatch Logs in batches.

    Three constraints worth knowing, because they are the ones that bite:
      * events in a batch MUST be sorted by timestamp, ascending;
      * a batch is at most 10,000 events and 1 MB;
      * sequence tokens were required until 2023 and are now ignored. If you
        find code carefully threading nextSequenceToken through a loop, it is
        old, and simplifying it is safe.
    """
    logs.create_log_stream(logGroupName=LOG_GROUP, logStreamName=stream_name)
    events.sort(key=lambda e: e["timestamp"])

    sent = 0
    batch, batch_bytes = [], 0
    for event in events:
        size = len(event["message"].encode("utf-8")) + 26  # 26B per-event overhead
        if len(batch) >= 8000 or batch_bytes + size > 900_000:
            logs.put_log_events(logGroupName=LOG_GROUP, logStreamName=stream_name, logEvents=batch)
            sent += len(batch)
            batch, batch_bytes = [], 0
        batch.append(event)
        batch_bytes += size
    if batch:
        logs.put_log_events(logGroupName=LOG_GROUP, logStreamName=stream_name, logEvents=batch)
        sent += len(batch)
    return sent


def handler(event, context):
    mode = event.get("mode", "cascade")
    lines = int(event.get("lines", DEFAULT_LINES))
    window_minutes = int(event.get("window_minutes", 12))
    include_cause = bool(event.get("include_cause", True))

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - window_minutes * 60 * 1000

    if mode == "normal":
        events = build_normal(start_ms, now_ms, lines)
    elif mode == "latency":
        events = build_latency_only(start_ms, now_ms, lines)
    else:
        mode = "cascade"
        events = build_cascade(start_ms, now_ms, lines, include_cause=include_cause)

    stream = "{}-{}".format(mode, time.strftime("%Y%m%d-%H%M%S", time.gmtime()))
    sent = put_events(stream, events)

    # This summary goes to the CHAOS function's own log group, not the workload
    # one, so it never pollutes the data the analyser reads. Keeping the
    # generator's output out of the generated data is a small discipline that
    # saves a confusing hour later.
    result = {
        "mode": mode,
        "log_group": LOG_GROUP,
        "log_stream": stream,
        "events_written": sent,
        "window_minutes": window_minutes,
        "cause_line_included": include_cause and mode == "cascade",
        "note": (
            "Metric filter datapoints carry the LOG EVENT timestamp, not the "
            "ingestion time, so backdated events appear at the right place on "
            "the graph. Alarms, however, evaluate on wall-clock periods, so an "
            "alarm may not transition until the window catches up."
        ),
    }
    print(json.dumps(result))
    return result
