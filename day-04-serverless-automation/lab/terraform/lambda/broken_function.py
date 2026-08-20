"""
Day 04 — the deliberately broken function
=========================================

This exists so serverless_audit.py has something real to find. It is created
only when create_insecure_examples = true.

Everything wrong with it is wrong on purpose, and every one of these mistakes
is something you will meet in a real account. They are not exotic. They live in
Terraform, in the console, in the deployment pipeline — which is exactly why an
automated configuration auditor earns its keep.

WHAT IS WRONG HERE, AND WHICH CHECK CATCHES IT

    CMP-001  No dead letter queue. When this times out, the event vanishes.
             No record it was ever attempted.
    CMP-002  Secrets in plaintext environment variables. See below.
    CMP-004  Its execution role grants Action "*" on Resource "*".
    CMP-005  No log group is created for it in Terraform, so Lambda makes one
             on first invocation with retention set to "Never expire".
    CMP-006  A 3-second timeout against a function that sleeps for 5.
    CMP-007  No reserved concurrency.
    CMP-009  No X-Ray tracing, so you cannot see where it died.

THE ENVIRONMENT VARIABLE POINT, WHICH PEOPLE UNDERESTIMATE

Lambda environment variables are not a secret store. They are visible to
anyone holding lambda:GetFunctionConfiguration — a permission that reads as
harmless in a policy review and is included in several AWS managed read-only
policies. Run:

    aws lambda get-function-configuration --function-name cbc-day04-broken-function

and the values come back in plaintext in the API response. No decryption step,
no KMS grant required, and no CloudTrail Decrypt event naming the caller. The
credential is simply readable, and you have no record that it was read.

Secrets Manager or SSM Parameter Store SecureString instead. Day 07 covers the
rotation story properly.

This function sleeps for 5 seconds so that, with the 3-second timeout, it
reliably times out. Invoke it and watch the Errors metric climb while nothing
lands anywhere, because there is no DLQ to catch it.
"""

import os
import time


def lambda_handler(event, context):
    # These are read from plaintext environment variables. Run
    #   aws lambda get-function-configuration --function-name <this>
    # and read them straight out of the response. No decryption, no KMS grant,
    # no CloudTrail record of a decrypt. Just lambda:GetFunction.
    api_key = os.environ.get("API_KEY", "")
    db_password = os.environ.get("DB_PASSWORD", "")

    print("Starting work. Key length:", len(api_key), "Password length:", len(db_password))

    # 5 seconds of work against a 3 second timeout.
    time.sleep(5)

    # This line never executes.
    return {"status": "ok", "message": "you will never see this"}
