"""
Day 06 — incident_analyser.py

Reads a window of CloudWatch logs around an alarm transition and produces an
incident summary.

===========================================================================
THE ARGUMENT THIS FILE IS MAKING
===========================================================================

    A summary you cannot check is worse than no summary.

An LLM handed a pile of log lines will produce fluent, confident, plausible
prose whether or not it understood anything. It will not hedge unless you make
hedging possible. It will not say "I don't know" unless you give it a way to
say that and a reason to believe you meant it. And a tired engineer at 03:00
will act on whatever it says, because it is the only thing on the screen that
is written in sentences.

"Can we summarise logs with an LLM" is solved, free, and demos beautifully.
It is not the engineering problem.

The engineering problem is: can a human disprove this summary in thirty
seconds? Everything unusual in this file exists to answer yes.

    1. DETERMINISTIC FIRST (deterministic_facts)
       Counts, rates, error-type breakdown, first and last occurrence, latency
       percentiles. Computed in Python from the raw events, with no model
       involved. These numbers go at the TOP of the output, above the prose.
       If the model is unavailable, wrong, or hallucinating, the numbers are
       still correct — and most of the time the numbers are the whole answer.

    2. SAMPLE HEAD + TAIL + STRATIFIED, NEVER TAIL-ONLY (sample_events)
       The obvious implementation is `events[-200:]`. It is also the reason
       this day exists. In a real cascade the CAUSE is at the beginning and
       the CONSEQUENCES are at the end, so tail-only truncation feeds the
       model a thousand symptoms and zero causes, and the model — having no
       way to know something is missing — confidently blames the symptom.
       See the demo in trainer-notes.md.

    3. A HARD TOKEN BUDGET, AND HONESTY ABOUT IT (fit_to_budget)
       The output states how many lines existed, how many were sent, and by
       what strategy. A summary based on 4% of the evidence should say so.

    4. REDACTION BEFORE THE PROMPT (redact)
       Best-effort, and labelled as best-effort. The real control is not
       logging the secret in the first place.

    5. CITATIONS THE CODE ACTUALLY CHECKS (verify_claims)
       This is the part that matters most and the part almost nobody builds.
       The model must return, for every claim, the index of a log line and a
       verbatim fragment from it. After the response comes back, this code
       looks up each index and checks the fragment really appears there.
       Claims that fail are marked UNVERIFIED and counted.

       A model cannot fake a citation that a `in` check will run against the
       exact text it was given. That single loop converts "sounds right" into
       "is checkable", which is the only difference that matters at 03:00.

    6. PERMISSION TO SAY NOTHING (the prompt contract)
       The schema has `insufficient_evidence`. The prompt says, in as many
       words, that returning it is a correct and expected answer. Without
       that, a model asked "what caused this" will always produce a cause,
       because producing a cause is what it was asked for.

===========================================================================
WHERE THIS SHOULD NOT BE USED
===========================================================================

Not for "how many 5xx did we serve" — a metric filter answers that exactly,
in milliseconds, for free, and will still be right next year.

Not for anything automated downstream. This output is for a human to read and
argue with. Wiring an LLM's root-cause guess into an automated remediation is
how you get a confident, fluent, well-cited rollback of the wrong service.

Not on unredacted logs from a system you do not own the data for.

Use it for exactly one question: "what happened here, and where should I look
first?" That question is genuinely hard, genuinely slow for a human at 03:00,
and the one place the model earns its cost.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config

# ---------------------------------------------------------------------------
# Configuration. Every one of these is a Terraform variable with a cost or a
# governance argument attached; see variables.tf.
# ---------------------------------------------------------------------------
WORKLOAD_LOG_GROUP = os.environ["WORKLOAD_LOG_GROUP"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
BEDROCK_REGION = os.environ["BEDROCK_REGION"]
SUMMARY_TOPIC_ARN = os.environ["SUMMARY_TOPIC_ARN"]

MAX_INPUT_TOKENS = int(os.environ.get("MAX_INPUT_TOKENS", "12000"))
MAX_LOG_LINES = int(os.environ.get("MAX_LOG_LINES", "600"))
LOOKBACK_MINUTES = int(os.environ.get("LOOKBACK_MINUTES", "30"))
REDACT_LOGS = os.environ.get("REDACT_LOGS", "true").lower() == "true"
IDEMPOTENCY_MINUTES = int(os.environ.get("IDEMPOTENCY_MINUTES", "15"))
IDEMPOTENCY_TABLE = os.environ.get("IDEMPOTENCY_TABLE", "")

# "balanced" = head + stratified middle + tail. "tail" = the naive
# implementation, kept switchable ON PURPOSE so trainer-notes.md can run the
# same pipeline twice and show it produce a correct answer and then a
# confident, fluent, completely wrong one. Nothing else changes between the
# two runs. See sample_events.
SAMPLE_STRATEGY = os.environ.get("SAMPLE_STRATEGY", "balanced")

# Retries are off for the model call on purpose. botocore's default adaptive
# retry will happily re-send a 12,000-token prompt three times on a throttle,
# and you pay for every attempt. Handle throttling by backing off the TRIGGER,
# not by hammering the model.
logs = boto3.client("logs")
sns = boto3.client("sns")
bedrock = boto3.client(
    "bedrock-runtime",
    region_name=BEDROCK_REGION,
    config=Config(retries={"max_attempts": 1, "mode": "standard"}, read_timeout=120),
)
ddb = boto3.client("dynamodb") if IDEMPOTENCY_TABLE else None


###############################################################################
# Redaction
#
# A regex pass over data whose shape you did not control. It catches the
# obvious, high-confidence secret shapes and nothing else.
#
# Read the list and notice what is NOT on it: a customer's name in a free-text
# field, a session ID your framework invented, an internal hostname, a stack
# trace with local variables still in it. None of those have a shape a regex
# can find, and all of them are in production logs somewhere right now.
#
# So: this is the seatbelt. The brakes are not logging it.
###############################################################################

_REDACTIONS = [
    (re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"), "[REDACTED-AWS-KEY-ID]"),
    (re.compile(r"\b[A-Za-z0-9/+=]{40}\b(?=.*secret)", re.I), "[REDACTED-POSSIBLE-SECRET]"),
    (re.compile(r"\bey[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[REDACTED-JWT]"),
    (re.compile(r"(?i)\b(bearer|token|api[_-]?key|password|passwd|secret)\b\s*[:=]\s*\S+"), r"\1=[REDACTED]"),
    (re.compile(r"\b[a-z]+://[^\s:@/]+:[^\s@/]+@"), "[REDACTED-CREDENTIALS-IN-URL]@"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[REDACTED-EMAIL]"),
    (re.compile(r"\b(?:\d[ -]*?){13,19}\b"), "[REDACTED-LONG-DIGIT-RUN]"),
]


def redact(text):
    """Best-effort. Returns (text, count_of_substitutions)."""
    if not REDACT_LOGS:
        return text, 0
    total = 0
    for pattern, replacement in _REDACTIONS:
        text, n = pattern.subn(replacement, text)
        total += n
    return text, total


###############################################################################
# Token budgeting
###############################################################################


def estimate_tokens(text):
    """Four characters per token.

    Crude, and deliberately so. The exact tokeniser differs per model and
    pulling one in as a dependency to be 8% more accurate about a number you
    are using as a SAFETY CEILING is the wrong trade. Round pessimistically
    and move on.

    If you need the real number, the API returns it: the usage block on the
    response tells you exactly what you were billed for. Log that, compare it
    to this estimate occasionally, and adjust the divisor if it drifts.
    """
    return (len(text) + 3) // 4


def fit_to_budget(lines, max_tokens):
    """Drop lines from the MIDDLE until the sample fits the token budget.

    From the middle, because sample_events has already arranged the list so
    that the head (the cause) and the tail (the current state) are the parts
    worth keeping. If you find yourself trimming from the front to fit a
    budget, stop and re-read the module docstring.
    """
    if max_tokens <= 0:
        # OBS-012: no budget. The tool still works; it is just now capable of
        # sending an unbounded prompt, and the cost of that is unbounded too.
        return lines, {"budget_applied": False, "reason": "MAX_INPUT_TOKENS=0 (no budget — OBS-012)"}

    kept = list(lines)
    dropped = 0
    # Track the joined length incrementally rather than re-joining the whole
    # list on every iteration. The naive version is O(n^2) on character count
    # and is genuinely slow at a few thousand lines — which is exactly when
    # you need it and exactly when you are paying Lambda duration for it.
    total_chars = sum(len(line) for line in kept) + max(0, len(kept) - 1)
    while kept and (total_chars + 3) // 4 > max_tokens:
        # Remove one from just past the head, preserving both ends.
        cut = min(len(kept) - 1, max(1, len(kept) // 2))
        total_chars -= len(kept.pop(cut)) + (1 if kept else 0)
        dropped += 1
    return kept, {
        "budget_applied": True,
        "max_tokens": max_tokens,
        "lines_dropped_to_fit": dropped,
        # One join at the end is cheap; one join per iteration was not.
        "estimated_tokens": estimate_tokens("\n".join(kept)),
    }


###############################################################################
# Sampling — the function that decides whether the answer can be right
###############################################################################


def sample_events(events, max_lines):
    """HEAD + STRATIFIED MIDDLE + TAIL.

    THE MISTAKE THIS EXISTS TO AVOID

        sample = events[-200:]

    That line is in a lot of production code and it is wrong for exactly the
    reason incidents are shaped the way they are. A cascade begins with one
    cause and ends with a thousand consequences. Keep the last 200 lines of a
    5,000-line incident and you keep 200 consequences and zero causes.

    The model then does what it was asked: it explains the consequences. It
    will blame the database, because every line it can see mentions the
    database. It will be fluent, structured, confident and wrong, and there
    will be nothing in its output to suggest that the answer is missing.

    THE STRATEGY

        head    the first 25% of the budget, in order. Deploys, config
                changes, the first WARN — the things that happened before
                anyone noticed. This is where causes live.
        middle  an evenly spaced sample of the remainder. Preserves the SHAPE
                of the incident: escalation, plateau, recovery attempts.
        tail    the last 25% of the budget. The current state, which is what
                the reader needs in order to decide whether it is still
                happening.

    Every sampled line keeps its ORIGINAL index. The model cites those
    indices, verify_claims resolves them, and the reader can go straight to
    the real line. A sample that renumbers its lines has thrown away the only
    thing that makes the citation checkable.
    """
    total = len(events)

    if SAMPLE_STRATEGY == "tail":
        # THE NAIVE IMPLEMENTATION, kept here deliberately.
        #
        # It is not a straw man. It is what almost everyone writes first,
        # because it is one line, it is obviously "the most recent data", and
        # it works fine on the incidents where the cause and the symptom are
        # the same thing. It fails silently on every cascade, which is most of
        # the incidents that page you.
        tail = list(enumerate(events))[-max_lines:]
        return tail, {
            "strategy": "tail-only (NAIVE — see sample_events)",
            "total_lines": total,
            "sampled_lines": len(tail),
            "coverage_pct": round(100.0 * len(tail) / total, 1) if total else 100.0,
            "warning": (
                "TAIL-ONLY SAMPLING. Everything before line "
                "{} was discarded, including anything that CAUSED this. The "
                "model cannot report a gap it was never told about."
            ).format(total - len(tail)),
        }

    if total <= max_lines:
        return list(enumerate(events)), {
            "strategy": "complete",
            "total_lines": total,
            "sampled_lines": total,
            "coverage_pct": 100.0,
        }

    head_n = max(1, max_lines // 4)
    tail_n = max(1, max_lines // 4)
    middle_n = max_lines - head_n - tail_n

    head = list(enumerate(events))[:head_n]
    tail = list(enumerate(events))[total - tail_n:]

    middle_pool = list(enumerate(events))[head_n: total - tail_n]
    if middle_n > 0 and middle_pool:
        step = max(1, len(middle_pool) // middle_n)
        middle = middle_pool[::step][:middle_n]
    else:
        middle = []

    sampled = head + middle + tail
    return sampled, {
        "strategy": "head+stratified+tail",
        "total_lines": total,
        "sampled_lines": len(sampled),
        "coverage_pct": round(100.0 * len(sampled) / total, 1),
        "head_lines": len(head),
        "middle_lines": len(middle),
        "tail_lines": len(tail),
        "warning": (
            "This summary is based on a SAMPLE. Lines between the sampled "
            "indices were not shown to the model. Anything it did not see, it "
            "cannot know it did not see."
        ),
    }


###############################################################################
# The deterministic half — no model, and most of the answer
###############################################################################


def _percentile(values, pct):
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    if lo == hi:
        return round(ordered[lo], 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 1)


def deterministic_facts(events):
    """Everything a metric filter and a Logs Insights query would tell you.

    Computed here, in Python, from the raw events, before any model is
    involved and regardless of whether one is involved at all.

    These go at the TOP of the notification, above the narrative, and that
    ordering is deliberate: the reader should meet the arithmetic before they
    meet the prose, so the prose has something to be checked against.

    In practice this block answers the incident on its own maybe two times in
    three. That is not a failure of the AI half. That is the AI half being
    correctly scoped to the cases where counting does not help.
    """
    levels = {}
    error_types = {}
    statuses = {}
    latencies = []
    first_error = None
    last_error = None
    config_events = []

    for idx, raw in enumerate(events):
        try:
            rec = json.loads(raw["message"])
        except (ValueError, KeyError):
            rec = {"level": "UNPARSED", "message": raw.get("message", "")}

        level = rec.get("level", "UNKNOWN")
        levels[level] = levels.get(level, 0) + 1

        if rec.get("error_type"):
            error_types[rec["error_type"]] = error_types.get(rec["error_type"], 0) + 1
        if rec.get("status") is not None:
            statuses[str(rec["status"])] = statuses.get(str(rec["status"]), 0) + 1
        if isinstance(rec.get("latency_ms"), (int, float)):
            latencies.append(float(rec["latency_ms"]))

        if level == "ERROR":
            if first_error is None:
                first_error = {"index": idx, "at": rec.get("ts"), "type": rec.get("error_type")}
            last_error = {"index": idx, "at": rec.get("ts"), "type": rec.get("error_type")}

        # Change events are searched for EXPLICITLY, because "what changed"
        # is the first question of every incident review and it does not need
        # a model to answer. If your logs do not contain deploy markers, the
        # highest-value observability change you can make this quarter is to
        # add them.
        if rec.get("event") in ("config_applied", "deploy_completed", "feature_flag_changed"):
            config_events.append({"index": idx, "at": rec.get("ts"), "detail": rec.get("message", "")})

    total_requests = sum(v for k, v in statuses.items())
    error_requests = sum(v for k, v in statuses.items() if k.startswith("5") or k.startswith("4"))

    return {
        "window_lines": len(events),
        "levels": levels,
        "error_types": error_types,
        "status_codes": statuses,
        "error_rate_pct": round(100.0 * error_requests / total_requests, 2) if total_requests else 0.0,
        "latency_ms": {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "max": round(max(latencies), 1) if latencies else None,
        },
        "first_error": first_error,
        "last_error": last_error,
        "change_events_in_window": config_events,
    }


###############################################################################
# The prompt
###############################################################################

SYSTEM_PROMPT = """\
You are an incident analyst reading raw application logs. You are writing for \
an on-call engineer who has just been paged, is tired, and will act on what \
you say.

Rules, in priority order:

1. EVERY factual claim must cite at least one numbered log line, and must \
include a short verbatim fragment copied EXACTLY from that line. A separate \
program checks your fragments against the real lines. A citation that does not \
match is worse than no claim at all, and it will be visibly marked as \
unverified in front of the reader.

2. If the evidence does not identify a cause, set "insufficient_evidence" to \
true and "root_cause" to null. This is a CORRECT and EXPECTED answer, not a \
failure. You have been given a SAMPLE of a larger log; the cause may simply \
not be in front of you. Saying so is more useful than guessing.

3. Distinguish CAUSE from CONSEQUENCE. In a cascade, most lines describe \
consequences. The cause is usually earlier, quieter, and often not an error at \
all — a deployment, a configuration change, a feature flag.

4. Do not recommend remediation beyond the next diagnostic step. You do not \
know the system's topology, its blast radius, or what else is running.

5. Return ONLY a JSON object. No preamble, no markdown fences, no commentary.

Schema:

{
  "summary": "2-4 sentences, plain language, no jargon the reader must decode",
  "root_cause": "one sentence, or null",
  "insufficient_evidence": true | false,
  "confidence": "high" | "medium" | "low",
  "timeline": [
    {"at": "timestamp from the line", "what": "what happened", "cite": [12]}
  ],
  "claims": [
    {"claim": "a single factual statement",
     "cite": [12],
     "quote": "verbatim fragment from line 12"}
  ],
  "recommended_next_check": "the single next thing a human should look at"
}
"""


def build_prompt(facts, sampled, sampling_stats, budget_stats):
    numbered = []
    redaction_count = 0
    for original_index, event in sampled:
        text, n = redact(event["message"])
        redaction_count += n
        numbered.append("[{}] {}".format(original_index, text))

    body = "\n".join(numbered)

    user_prompt = (
        "DETERMINISTIC FACTS (computed from the full window, not from the sample "
        "below — trust these over anything you infer):\n"
        + json.dumps(facts, indent=2)
        + "\n\nSAMPLING (read this before you conclude anything):\n"
        + json.dumps(sampling_stats, indent=2)
        + "\n"
        + json.dumps(budget_stats, indent=2)
        + "\n\nLOG LINES. The number in brackets is the line's index in the FULL "
        "window, not in this sample. Cite those numbers.\n\n"
        + body
    )
    return user_prompt, numbered, redaction_count


###############################################################################
# Citation verification — the loop that makes the summary checkable
###############################################################################


def _normalise(text):
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_claims(parsed, numbered):
    """Resolve every citation and check the quoted fragment really appears.

    This is thirty lines of code and it is the difference between a demo and a
    tool. A model can produce a confident sentence about a log line that does
    not exist. It cannot produce a fragment that survives an `in` check
    against the exact text it was shown.

    Anything that fails is not deleted — it is MARKED. Hiding the model's
    failures from the reader is the same mistake as trusting them.
    """
    index_to_line = {}
    for entry in numbered:
        match = re.match(r"^\[(\d+)\]\s(.*)$", entry, re.S)
        if match:
            index_to_line[int(match.group(1))] = match.group(2)

    verified = 0
    total = 0
    for claim in parsed.get("claims", []):
        total += 1
        cites = claim.get("cite") or []
        quote = _normalise(claim.get("quote", ""))
        ok = False
        problems = []

        if not cites:
            problems.append("no citation given")
        if not quote:
            problems.append("no verbatim quote given")

        for cite in cites:
            line = index_to_line.get(cite)
            if line is None:
                problems.append("line {} was not in the sample shown to the model".format(cite))
                continue
            if quote and quote in _normalise(line):
                ok = True
            elif quote:
                problems.append("quoted text does not appear in line {}".format(cite))

        claim["verified"] = ok
        if not ok:
            claim["verification_problems"] = problems
        else:
            verified += 1

    parsed["grounding"] = {
        "claims_total": total,
        "claims_verified": verified,
        "claims_unverified": total - verified,
        "grounding_pct": round(100.0 * verified / total, 1) if total else None,
        "note": (
            "Every claim above was checked against the exact log text the model "
            "was given. UNVERIFIED means the quoted fragment could not be found "
            "at the cited line. Read those claims with suspicion; they are the "
            "ones most likely to be invented."
        ),
    }
    return parsed


###############################################################################
# Bedrock
###############################################################################


def invoke_model(user_prompt):
    """One call, no retries, usage recorded.

    The Converse API is used instead of InvokeModel because the request and
    response shapes are the same across model families — so changing
    bedrock_model_id does not mean rewriting this function. The IAM action is
    still bedrock:InvokeModel; Converse does not have its own.
    """
    started = time.time()
    response = bedrock.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": [{"text": user_prompt}]}],
        inferenceConfig={
            # Small output on purpose. The schema is short and a long answer
            # to a short schema is a sign the model is padding.
            "maxTokens": 1500,
            # Low temperature: this is an extraction task, not a creative one.
            # Nothing about incident analysis benefits from variety.
            "temperature": 0.0,
        },
    )
    elapsed_ms = int((time.time() - started) * 1000)
    usage = response.get("usage", {})
    text = response["output"]["message"]["content"][0]["text"]
    return text, {
        "model_id": MODEL_ID,
        "region": BEDROCK_REGION,
        "latency_ms": elapsed_ms,
        "input_tokens": usage.get("inputTokens"),
        "output_tokens": usage.get("outputTokens"),
        # Log the real token counts next to the estimate. If they diverge
        # badly, estimate_tokens' divisor needs adjusting — that is the only
        # honest way to calibrate it.
        "note": "Compare input_tokens with the estimate in budget stats.",
    }


def parse_model_json(text):
    """Models add fences and preamble no matter how firmly you ask them not to.

    Strip what is strippable, then fail loudly. A parse failure must NOT be
    swallowed into a default summary — a fabricated empty analysis presented
    as an analysis is exactly the failure mode this whole file exists to
    prevent.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("model returned no JSON object")
    return json.loads(cleaned[start:end + 1])


###############################################################################
# Idempotency — the guard between a flapping alarm and a four-figure weekend
###############################################################################


def already_summarised(alarm_name):
    """Conditional-write lock with a TTL.

    An alarm on a noisy metric can transition dozens of times an hour. Without
    this, every transition is a paid model invocation, all night, while
    nothing is actually broken and no dashboard turns red.

    The TTL attribute means DynamoDB deletes expired rows for free — TTL
    deletions are not billed as writes. Do not build this with a scheduled
    cleanup Lambda.
    """
    if not ddb or IDEMPOTENCY_MINUTES <= 0:
        return False
    now = int(time.time())
    try:
        ddb.put_item(
            TableName=IDEMPOTENCY_TABLE,
            Item={
                "alarm_name": {"S": alarm_name},
                "summarised_at": {"N": str(now)},
                "expires_at": {"N": str(now + IDEMPOTENCY_MINUTES * 60)},
            },
            ConditionExpression="attribute_not_exists(alarm_name) OR expires_at < :now",
            ExpressionAttributeValues={":now": {"N": str(now)}},
        )
        return False
    except ddb.exceptions.ConditionalCheckFailedException:
        return True


###############################################################################
# Fetching the window
###############################################################################


def fetch_events(start_ms, end_ms, limit):
    """filter_log_events, not start_query.

    Logs Insights (start_query/get_query_results) is the better tool for
    interactive work and it is what the lab has you run by hand. Inside a
    Lambda it is awkward: the call is asynchronous, so you poll, and you are
    paying Lambda duration for every second of that poll.

    filter_log_events is synchronous and paginated, and at this window size it
    is both cheaper and simpler. Logs Insights earns its keep when you need
    `stats ... by ...` across a lot of data — not for "give me the lines".

    Cost note: filter_log_events is free; Logs Insights is $0.005/GB scanned.
    Neither is the expensive part of this function.
    """
    events = []
    kwargs = {
        "logGroupName": WORKLOAD_LOG_GROUP,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": min(limit, 10000),
    }
    paginator = logs.get_paginator("filter_log_events")
    for page in paginator.paginate(**kwargs):
        for event in page.get("events", []):
            events.append({"timestamp": event["timestamp"], "message": event["message"]})
            if len(events) >= limit:
                break
        if len(events) >= limit:
            break
    events.sort(key=lambda e: e["timestamp"])
    return events


###############################################################################
# Handler
###############################################################################


def handler(event, context):
    # EventBridge alarm state change, or a manual invocation with the same
    # shape. Both paths are supported because Step 6 of the lab runs this by
    # hand before anything is wired up.
    detail = event.get("detail", {})
    alarm_name = event.get("alarmName") or detail.get("alarmName") or "manual-invocation"
    state = detail.get("state", {}).get("value", "MANUAL")
    reason = detail.get("state", {}).get("reason", "invoked by hand")

    if state == "OK":
        return {"skipped": "alarm returned to OK; nothing to analyse"}

    if already_summarised(alarm_name):
        # Not an error. This is the guard working.
        return {
            "skipped": "idempotency",
            "alarm": alarm_name,
            "window_minutes": IDEMPOTENCY_MINUTES,
            "note": "This alarm was already summarised inside the idempotency window. "
                    "A flapping alarm costs one invocation per window, not one per flap.",
        }

    end_ms = int(time.time() * 1000)
    if event.get("end_time_ms"):
        end_ms = int(event["end_time_ms"])
    lookback = int(event.get("lookback_minutes", LOOKBACK_MINUTES))
    start_ms = end_ms - lookback * 60 * 1000

    events = fetch_events(start_ms, end_ms, MAX_LOG_LINES)
    if not events:
        return {
            "alarm": alarm_name,
            "result": "no log events in window",
            "window": [start_ms, end_ms],
            "note": "An empty window is itself a finding. Check that the workload is "
                    "writing where you think it is.",
        }

    facts = deterministic_facts(events)
    sampled, sampling_stats = sample_events(events, MAX_LOG_LINES)

    user_prompt, numbered, redaction_count = build_prompt(facts, sampled, sampling_stats, {})
    budgeted_lines, budget_stats = fit_to_budget(numbered, MAX_INPUT_TOKENS)
    budget_stats["redactions_applied"] = redaction_count
    budget_stats["redaction_enabled"] = REDACT_LOGS
    if not REDACT_LOGS:
        budget_stats["warning"] = (
            "REDACTION IS OFF (OBS-011). Raw log text was sent to the model verbatim."
        )

    # Rebuild the prompt from the budgeted line set so what we verify against
    # is exactly what was sent. Verifying against a different list than the
    # model saw is a subtle and very convincing bug.
    user_prompt, _, _ = build_prompt(facts, [], sampling_stats, budget_stats)
    user_prompt = user_prompt + "\n" + "\n".join(budgeted_lines)

    try:
        raw_text, model_stats = invoke_model(user_prompt)
        parsed = parse_model_json(raw_text)
        parsed = verify_claims(parsed, budgeted_lines)
        model_error = None
    except Exception as exc:  # noqa: BLE001 — deliberate, see below
        # The deterministic facts are still correct and still useful. Failing
        # the whole invocation because the narrative half broke would throw
        # away the part that was never in doubt.
        parsed = None
        model_stats = {}
        model_error = "{}: {}".format(type(exc).__name__, exc)

    result = {
        "alarm": alarm_name,
        "alarm_state": state,
        "alarm_reason": reason,
        "window_utc": [
            datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat(),
            datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat(),
        ],
        "deterministic_facts": facts,
        "sampling": sampling_stats,
        "budget": budget_stats,
        "model": model_stats,
        "analysis": parsed,
        "model_error": model_error,
    }

    sns.publish(
        TopicArn=SUMMARY_TOPIC_ARN,
        Subject="[{}] incident summary: {}".format(state, alarm_name)[:100],
        Message=render_notification(result),
    )
    print(json.dumps(result, default=str))
    return result


def render_notification(result):
    """Numbers first, prose second, unverified claims flagged loudly.

    The ordering is the argument. A reader who meets the narrative first
    reads the numbers as confirmation of it. A reader who meets the numbers
    first reads the narrative as a hypothesis about them. Same content,
    completely different epistemics, and it costs nothing to get right.
    """
    facts = result["deterministic_facts"]
    lines = [
        "INCIDENT SUMMARY — {}".format(result["alarm"]),
        "Window (UTC): {} .. {}".format(*result["window_utc"]),
        "",
        "== MEASURED (no model involved; these are arithmetic) ==",
        "  lines in window : {}".format(facts["window_lines"]),
        "  levels          : {}".format(facts["levels"]),
        "  error rate      : {}%".format(facts["error_rate_pct"]),
        "  error types     : {}".format(facts["error_types"] or "none"),
        "  latency ms      : {}".format(facts["latency_ms"]),
        "  first error     : {}".format(facts["first_error"] or "none"),
        "  changes in window: {}".format(
            facts["change_events_in_window"] or "NONE FOUND — if your logs have no deploy markers, add them"
        ),
        "",
        "== SAMPLED ==",
        "  {} of {} lines ({}%), strategy: {}".format(
            result["sampling"]["sampled_lines"],
            result["sampling"]["total_lines"],
            result["sampling"]["coverage_pct"],
            result["sampling"]["strategy"],
        ),
    ]

    if result.get("model_error"):
        lines += [
            "",
            "== NARRATIVE ==",
            "  MODEL CALL FAILED: {}".format(result["model_error"]),
            "  Everything above is still correct. Work from it.",
        ]
        return "\n".join(lines)

    analysis = result.get("analysis") or {}
    grounding = analysis.get("grounding", {})
    lines += [
        "",
        "== GENERATED NARRATIVE (a hypothesis, not a finding) ==",
        "  confidence: {}   grounding: {}/{} claims verified".format(
            analysis.get("confidence", "?"),
            grounding.get("claims_verified", 0),
            grounding.get("claims_total", 0),
        ),
        "",
        "  {}".format(analysis.get("summary", "(none)")),
        "",
        "  root cause: {}".format(
            "INSUFFICIENT EVIDENCE" if analysis.get("insufficient_evidence") else analysis.get("root_cause")
        ),
        "  next check: {}".format(analysis.get("recommended_next_check", "(none)")),
        "",
        "  claims:",
    ]
    for claim in analysis.get("claims", []):
        mark = "OK " if claim.get("verified") else "UNVERIFIED"
        lines.append("    [{}] {}  (line {})".format(mark, claim.get("claim"), claim.get("cite")))
        if not claim.get("verified"):
            lines.append("             ^ {}".format("; ".join(claim.get("verification_problems", []))))

    if grounding.get("claims_unverified"):
        lines += [
            "",
            "  *** {} claim(s) could not be verified against the log lines the".format(
                grounding["claims_unverified"]
            ),
            "  *** model was shown. Treat those as invented until you check them.",
        ]

    lines += [
        "",
        "This summary is a starting point for a human, not a conclusion and not",
        "an input to automation. Check the cited lines before you act.",
    ]
    return "\n".join(lines)
