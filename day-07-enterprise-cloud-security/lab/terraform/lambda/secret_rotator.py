"""
Day 07 — secret_rotator.py

A Secrets Manager rotation function implementing the full four-step protocol.

READ THIS FIRST
===============

This is a TEACHING IMPLEMENTATION. It performs a genuine four-step rotation of
a JSON secret value, with correct staging-label handling, correct idempotency,
and correct failure behaviour. What it does NOT do is push the new credential
to a real service, because this lab has no database to push it to.

The place where that would happen — `set_secret` — is marked, loudly. If you
copy this file, that function is the part you must write, and leaving it as a
no-op produces the most dangerous outcome in this whole subject:

    Rotation succeeds every time. LastRotatedDate updates. The console is
    green. And the credential in the database never changed, so the value your
    application now fetches does not work.

You have built a scheduled outage that reports itself as compliant.

THE PROTOCOL
============

Secrets Manager invokes this function FOUR times per rotation, with the same
`SecretId` and `ClientRequestToken` and a different `Step` each time. The whole
design exists so that a rotation which fails halfway leaves a WORKING
credential behind.

    createSecret   Generate the new value; store it labelled AWSPENDING.
                   AWSCURRENT is untouched and applications keep working.
                   MUST BE IDEMPOTENT — this step is retried, and generating a
                   fresh password on every retry is how you end up with a
                   pending version nobody can test.

    setSecret      Push AWSPENDING to the actual service. ALTER USER, the
                   provider's API, the SSH authorized_keys file, whatever the
                   credential is for. THIS IS THE REAL WORK.

    testSecret     Connect using AWSPENDING. If this raises, rotation stops
                   here, AWSCURRENT is never moved, and the old credential
                   keeps working. This step is the entire reason the protocol
                   has four steps instead of one.

    finishSecret   Move the AWSCURRENT label onto the pending version. Only
                   now do applications start receiving the new value.

STAGING LABELS
==============

Three labels matter and they are the mechanism, not decoration:

    AWSCURRENT     What `get_secret_value` returns when you do not ask for a
                   version. This is what your applications get.
    AWSPENDING     The candidate. Exists only during a rotation.
    AWSPREVIOUS    Automatically applied to the old AWSCURRENT when the label
                   moves, so there is exactly one generation of rollback.

`update_secret_version_stage` with both `MoveToVersionId` and
`RemoveFromVersionId` is an atomic swap. Doing it in two calls is a window in
which the secret has no AWSCURRENT at all, and every application fetching
during that window fails.

WHAT TO CHECK WHEN SOMEBODY SAYS ROTATION IS WORKING
====================================================

    aws secretsmanager describe-secret --secret-id <id> \\
      --query '{Enabled:RotationEnabled,Last:LastRotatedDate,Rules:RotationRules}'

`RotationEnabled: true` means a schedule exists. `LastRotatedDate` is the only
field that means rotation actually ran. Absent, or far older than
`AutomaticallyAfterDays` implies, means it has been failing — silently, since
whenever. That is check SEC-011 and it is the check most likely to fire in an
account that believes it is fine.

AND THE THING ROTATION DOES NOT FIX
===================================

Rotating a credential does nothing about the copies of the OLD one.

If the application logged its connection string on startup — once, in March,
into a CloudWatch log group set to Never expire — then eleven rotations later
that March value is still sitting there, readable by anyone with CloudWatch
read access. Rotation has given you eleven credentials to worry about instead
of one.

That is Day 06's OBS-011 seen from the other side, and it is why the day
README pairs credential hygiene with log hygiene rather than treating them as
separate subjects.
"""

import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

secrets = boto3.client(
    "secretsmanager",
    endpoint_url=os.environ.get("SECRETS_MANAGER_ENDPOINT") or None,
)


def handler(event, context):
    """Dispatch on Step. Every branch validates the request before acting."""
    secret_id = event["SecretId"]
    token = event["ClientRequestToken"]
    step = event["Step"]

    metadata = secrets.describe_secret(SecretId=secret_id)

    if not metadata.get("RotationEnabled"):
        raise ValueError(f"Secret {secret_id} is not enabled for rotation")

    versions = metadata["VersionIdsToStages"]

    if token not in versions:
        raise ValueError(f"Version {token} has no stage for secret {secret_id}")

    if "AWSCURRENT" in versions[token]:
        # Already finished. Secrets Manager retries steps, and returning
        # quietly here is what makes the whole function safe to re-invoke.
        logger.info("Version %s is already AWSCURRENT; nothing to do", token)
        return

    if "AWSPENDING" not in versions[token]:
        raise ValueError(f"Version {token} is not AWSPENDING for secret {secret_id}")

    logger.info("Step %s for secret %s version %s", step, secret_id, token)

    if step == "createSecret":
        create_secret(secret_id, token)
    elif step == "setSecret":
        set_secret(secret_id, token)
    elif step == "testSecret":
        test_secret(secret_id, token)
    elif step == "finishSecret":
        finish_secret(secret_id, token)
    else:
        raise ValueError(f"Unknown rotation step: {step}")


def create_secret(secret_id, token):
    """Generate the candidate value and store it as AWSPENDING.

    IDEMPOTENCY IS THE WHOLE POINT OF THE FIRST CHECK. Secrets Manager retries
    this step. Generating a fresh password on every retry means the value you
    tested is not the value you finish with, and the symptom is a rotation that
    succeeds and leaves an unusable credential.
    """
    try:
        secrets.get_secret_value(SecretId=secret_id, VersionId=token, VersionStage="AWSPENDING")
        logger.info("AWSPENDING already exists for %s; leaving it alone", token)
        return
    except secrets.exceptions.ResourceNotFoundException:
        pass

    current = json.loads(
        secrets.get_secret_value(SecretId=secret_id, VersionStage="AWSCURRENT")["SecretString"]
    )

    # GetRandomPassword rather than a local RNG: it is auditable in CloudTrail,
    # it honours the exclusion rules services actually impose, and it removes
    # any argument about entropy source during a review.
    new_password = secrets.get_random_password(
        PasswordLength=32,
        ExcludeCharacters='/@"\\\'',
        RequireEachIncludedType=True,
    )["RandomPassword"]

    candidate = dict(current)
    candidate["password"] = new_password

    secrets.put_secret_value(
        SecretId=secret_id,
        ClientRequestToken=token,
        SecretString=json.dumps(candidate),
        VersionStages=["AWSPENDING"],
    )
    logger.info("Created AWSPENDING version for %s", secret_id)


def set_secret(secret_id, token):
    """>>> THIS IS WHERE THE REAL WORK GOES. <<<

    Push the AWSPENDING value to the service that actually authenticates with
    it: ALTER USER against the database, a PUT against the provider's API, a
    rewrite of authorized_keys, whatever the credential is for.

    This lab has no such service, so this step is a NO-OP and says so. That is
    an acceptable thing for a teaching implementation and an unacceptable thing
    for a real one, because a rotator that stubs this step reports success
    forever while the credential never changes.

    If you copy this file and ship it with this function still empty, you have
    built a scheduled outage that passes its own compliance check.
    """
    pending = json.loads(
        secrets.get_secret_value(SecretId=secret_id, VersionId=token, VersionStage="AWSPENDING")[
            "SecretString"
        ]
    )
    logger.warning(
        "setSecret is a NO-OP in this teaching implementation. A real rotator "
        "would now apply the new credential for user %r to %r. Nothing has "
        "been changed on any downstream service.",
        pending.get("username"),
        pending.get("host"),
    )


def test_secret(secret_id, token):
    """Prove the pending credential works BEFORE it becomes current.

    In a real rotator this opens a connection with the pending value and runs
    the cheapest possible statement — `SELECT 1`, an authenticated GET, a
    no-op API call. If it raises, rotation stops here and AWSCURRENT is never
    moved, which means the old credential is still working and nobody is
    paged.

    Skipping this step is the second most common rotator bug, after stubbing
    setSecret. It converts a caught failure into an outage.
    """
    pending = json.loads(
        secrets.get_secret_value(SecretId=secret_id, VersionId=token, VersionStage="AWSPENDING")[
            "SecretString"
        ]
    )

    required = ("username", "password", "engine", "host")
    missing = [field for field in required if not pending.get(field)]
    if missing:
        raise ValueError(f"pending secret is missing required fields: {missing}")
    if pending["password"].startswith("REPLACE-ME"):
        raise ValueError("pending secret still holds the placeholder password")

    logger.info("Structural validation of the pending value passed. A real "
                "rotator would now CONNECT with it.")


def finish_secret(secret_id, token):
    """Move AWSCURRENT onto the pending version. Atomically.

    Both MoveToVersionId and RemoveFromVersionId in ONE call. Doing it as two
    calls leaves a window with no AWSCURRENT at all, and every application
    fetching during that window fails. The window is short and the failure is
    intermittent, which makes it one of the harder bugs to reproduce.
    """
    metadata = secrets.describe_secret(SecretId=secret_id)

    current_version = None
    for version, stages in metadata["VersionIdsToStages"].items():
        if "AWSCURRENT" in stages:
            if version == token:
                logger.info("Version %s is already AWSCURRENT", token)
                return
            current_version = version
            break

    secrets.update_secret_version_stage(
        SecretId=secret_id,
        VersionStage="AWSCURRENT",
        MoveToVersionId=token,
        RemoveFromVersionId=current_version,
    )
    logger.info("AWSCURRENT moved to %s; %s is now AWSPREVIOUS", token, current_version)
