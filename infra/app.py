#!/usr/bin/env python3
import os

import aws_cdk as cdk

from payroll_stack import PayrollStack

app = cdk.App()
PayrollStack(
    app,
    "Payroll",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "eu-central-1"),
    ),
)
app.synth()
