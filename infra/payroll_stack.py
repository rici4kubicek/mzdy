"""Serverless infrastructure: CloudFront + S3 frontend, HTTP API + Lambda, DynamoDB, SQS, SES."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import jsii
from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    DockerImage,
    Duration,
    ILocalBundling,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigatewayv2 as apigw
from aws_cdk import aws_apigatewayv2_integrations as integrations
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_lambda_event_sources as event_sources
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_deployment as s3deploy
from aws_cdk import aws_ses as ses
from aws_cdk import aws_sqs as sqs
from constructs import Construct

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND_DIST = ROOT / "frontend" / "dist"
JWT_SECRET_PARAM = "/payroll/jwt-secret"
RUNTIME = lambda_.Runtime.PYTHON_3_12


def _bundle_commands(source: str, output: str) -> str:
    """Install the locked runtime dependencies and the backend package with uv.

    All runtime dependencies are pure Python; the platform flags only make sure uv
    resolves wheels for the Lambda runtime rather than for the build host.
    """
    target = (
        f"--target {output} --no-compile --python-version 3.12 "
        "--python-platform aarch64-manylinux2014"
    )
    return " && ".join(
        [
            f"cd {source}",
            "uv export --frozen --no-dev --no-emit-project --quiet -o /tmp/payroll-requirements.txt",
            f"uv pip install --quiet {target} -r /tmp/payroll-requirements.txt",
            f"uv pip install --quiet --no-deps {target} .",
        ]
    )


@jsii.implements(ILocalBundling)
class _LocalUvBundling:
    """Bundle the backend with uv on the host so that Docker is not required."""

    def try_bundle(self, output_dir: str, *, image=None, **_kwargs) -> bool:
        if shutil.which("uv") is None:
            return False
        subprocess.run(["bash", "-c", _bundle_commands(str(BACKEND), output_dir)], check=True)
        return True


def _backend_code() -> lambda_.Code:
    return lambda_.Code.from_asset(
        str(BACKEND),
        exclude=[".venv", "tests", "**/__pycache__", ".pytest_cache", ".ruff_cache", "build"],
        bundling=BundlingOptions(
            image=DockerImage.from_registry(RUNTIME.bundling_image.image),
            command=[
                "bash",
                "-c",
                "pip install --quiet uv && "
                + _bundle_commands("/asset-input", "/asset-output"),
            ],
            local=_LocalUvBundling(),
        ),
    )


# Rewrites client-side routes (/batches/2026-10) to index.html for the SPA.
_SPA_REWRITE = """
function handler(event) {
  var request = event.request;
  if (!request.uri.includes('.')) {
    request.uri = '/index.html';
  }
  return request;
}
"""


class PayrollStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        retention_days = int(self.node.try_get_context("retentionDays") or 730)
        sender_domain = self.node.try_get_context("senderDomain") or ""

        # --- storage --------------------------------------------------------

        table = dynamodb.TableV2(
            self,
            "Table",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="sk", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            point_in_time_recovery_specification=dynamodb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True
            ),
            removal_policy=RemovalPolicy.RETAIN,
        )

        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(prefix="batches/", expiration=Duration.days(retention_days)),
            ],
            # Payslip PDFs are uploaded from the browser with presigned URLs; the
            # signature, not the origin, is what authorizes the request.
            cors=[
                s3.CorsRule(
                    allowed_methods=[s3.HttpMethods.PUT],
                    allowed_origins=["*"],
                    allowed_headers=["content-type"],
                    max_age=3000,
                )
            ],
        )

        # --- e-mail queue ---------------------------------------------------

        dead_letters = sqs.Queue(
            self,
            "PayslipDeadLetters",
            retention_period=Duration.days(14),
            enforce_ssl=True,
        )
        queue = sqs.Queue(
            self,
            "PayslipQueue",
            visibility_timeout=Duration.minutes(3),
            enforce_ssl=True,
            dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dead_letters),
        )

        # --- functions ------------------------------------------------------

        code = _backend_code()
        common_env = {
            "TABLE_NAME": table.table_name,
            "BUCKET_NAME": data_bucket.bucket_name,
            "JWT_SECRET_PARAM": JWT_SECRET_PARAM,
            "QUEUE_URL": queue.queue_url,
        }

        api_fn = lambda_.Function(
            self,
            "ApiFunction",
            runtime=RUNTIME,
            architecture=lambda_.Architecture.ARM_64,
            handler="payroll.api.app.handler",
            code=code,
            memory_size=1024,
            timeout=Duration.seconds(29),
            environment=common_env,
            log_group=logs.LogGroup(
                self, "ApiLogs", retention=logs.RetentionDays.THREE_MONTHS
            ),
        )
        table.grant_read_write_data(api_fn)
        data_bucket.grant_read_write(api_fn)
        queue.grant_send_messages(api_fn)
        api_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    self.format_arn(
                        service="ssm", resource="parameter", resource_name=JWT_SECRET_PARAM[1:]
                    )
                ],
            )
        )

        mailer_fn = lambda_.Function(
            self,
            "MailerFunction",
            runtime=RUNTIME,
            architecture=lambda_.Architecture.ARM_64,
            handler="payroll.mailer.handler",
            code=code,
            memory_size=256,
            timeout=Duration.seconds(30),
            environment=common_env,
            log_group=logs.LogGroup(
                self, "MailerLogs", retention=logs.RetentionDays.THREE_MONTHS
            ),
        )
        table.grant_read_write_data(mailer_fn)
        data_bucket.grant_read(mailer_fn)
        mailer_fn.add_to_role_policy(
            iam.PolicyStatement(actions=["ses:SendRawEmail"], resources=["*"])
        )
        mailer_fn.add_event_source(
            event_sources.SqsEventSource(
                queue,
                batch_size=5,
                report_batch_item_failures=True,
                max_concurrency=2,
            )
        )

        # --- HTTP API -------------------------------------------------------

        http_api = apigw.HttpApi(self, "HttpApi", create_default_stage=False)
        http_api.add_routes(
            path="/api/{proxy+}",
            methods=[apigw.HttpMethod.ANY],
            integration=integrations.HttpLambdaIntegration("ApiIntegration", api_fn),
        )
        apigw.HttpStage(
            self,
            "DefaultStage",
            http_api=http_api,
            stage_name="$default",
            auto_deploy=True,
            throttle=apigw.ThrottleSettings(rate_limit=20, burst_limit=40),
        )

        # --- frontend -------------------------------------------------------

        site_bucket = s3.Bucket(
            self,
            "SiteBucket",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        security_headers = cloudfront.ResponseHeadersPolicy(
            self,
            "SecurityHeaders",
            security_headers_behavior=cloudfront.ResponseSecurityHeadersBehavior(
                strict_transport_security=cloudfront.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(365),
                    include_subdomains=True,
                    override=True,
                ),
                content_type_options=cloudfront.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cloudfront.ResponseHeadersFrameOptions(
                    frame_option=cloudfront.HeadersFrameOption.DENY, override=True
                ),
                referrer_policy=cloudfront.ResponseHeadersReferrerPolicy(
                    referrer_policy=cloudfront.HeadersReferrerPolicy.SAME_ORIGIN, override=True
                ),
            ),
        )

        api_domain = f"{http_api.api_id}.execute-api.{self.region}.{self.url_suffix}"
        distribution = cloudfront.Distribution(
            self,
            "Distribution",
            default_root_object="index.html",
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site_bucket),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=security_headers,
                function_associations=[
                    cloudfront.FunctionAssociation(
                        event_type=cloudfront.FunctionEventType.VIEWER_REQUEST,
                        function=cloudfront.Function(
                            self,
                            "SpaRewrite",
                            code=cloudfront.FunctionCode.from_inline(_SPA_REWRITE),
                            runtime=cloudfront.FunctionRuntime.JS_2_0,
                        ),
                    )
                ],
            ),
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=origins.HttpOrigin(api_domain),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    response_headers_policy=security_headers,
                )
            },
        )

        if FRONTEND_DIST.is_dir():
            s3deploy.BucketDeployment(
                self,
                "DeploySite",
                sources=[s3deploy.Source.asset(str(FRONTEND_DIST))],
                destination_bucket=site_bucket,
                distribution=distribution,
                distribution_paths=["/*"],
            )

        # --- e-mail identity ------------------------------------------------

        if sender_domain:
            identity = ses.EmailIdentity(
                self, "SenderIdentity", identity=ses.Identity.domain(sender_domain)
            )
            for i, record in enumerate(
                [
                    (identity.dkim_dns_token_name1, identity.dkim_dns_token_value1),
                    (identity.dkim_dns_token_name2, identity.dkim_dns_token_value2),
                    (identity.dkim_dns_token_name3, identity.dkim_dns_token_value3),
                ],
                start=1,
            ):
                CfnOutput(self, f"DkimRecord{i}", value=f"{record[0]} CNAME {record[1]}")

        CfnOutput(self, "Url", value=f"https://{distribution.distribution_domain_name}")
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "DataBucketName", value=data_bucket.bucket_name)
        CfnOutput(self, "DeadLetterQueueUrl", value=dead_letters.queue_url)
