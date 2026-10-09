import json

import boto3
from botocore.exceptions import ClientError

from app.core.logging import get_logger

logger = get_logger(__name__)


class SsmClient:
    """Thin synchronous wrapper around SSM Parameter Store."""

    def get_json(self, parameter_name: str) -> dict:
        try:
            resp = boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=False)
            return json.loads(resp["Parameter"]["Value"])
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ParameterNotFound":
                raise FileNotFoundError(f"SSM parameter not found: {parameter_name}") from exc
            raise RuntimeError(f"SSM GetParameter failed ({code}): {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"SSM parameter {parameter_name!r} contains invalid JSON") from exc

    def put_json(self, parameter_name: str, value: dict) -> None:
        try:
            boto3.client("ssm").put_parameter(
                Name=parameter_name,
                Value=json.dumps(value, indent=2),
                Type="String",
                Overwrite=True,
            )
            logger.info("config written to SSM", parameter=parameter_name)
        except ClientError as exc:
            raise RuntimeError(f"SSM PutParameter failed: {exc}") from exc
