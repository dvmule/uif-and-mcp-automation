#!/usr/bin/env python3
# register_secret.py
# Creates or updates an Oracle JDBC credential secret in Secrets Manager.
# One secret per Oracle DB source — all pipelines on the same DB share one secret.
#
# Usage:
#   aws-runas ingestion-deploy python scripts/register_secret.py \
#     --name     oracle-erp \
#     --username INGEST_USER \
#     --password "s3cr3t!" \
#     --host     erp-db.internal.example.com \
#     --port     1521 \
#     --service  ERPDB
#
# The secret is stored as JSON:
#   {
#     "username": "INGEST_USER",
#     "password": "s3cr3t!",
#     "host":     "erp-db.internal.example.com",
#     "port":     "1521",
#     "service":  "ERPDB"
#   }
#
# The Glue job builds the JDBC URL from these fields:
#   jdbc:oracle:thin:@//host:port/service

import argparse
import json
import sys

import boto3
from botocore.exceptions import ClientError

REGION = "eu-west-1"


def build_secret_value(args) -> str:
    return json.dumps({
        "username": args.username,
        "password": args.password,
        "host":     args.host,
        "port":     str(args.port),
        "service":  args.service,
        "dsn":      f"//{args.host}:{args.port}/{args.service}",
    })


def register(args):
    client = boto3.client("secretsmanager", region_name=REGION)
    secret_value = build_secret_value(args)

    # Name must start with a letter or underscore — prefix with "oracle-" if not already
    name = args.name if args.name.startswith("oracle-") else f"oracle-{args.name}"

    try:
        resp = client.create_secret(
            Name        = name,
            Description = f"Oracle JDBC credentials for {args.host}/{args.service}",
            SecretString= secret_value,
            Tags=[
                {"Key": "Project",   "Value": "eds"},
                {"Key": "ManagedBy", "Value": "register_secret.py"},
            ],
        )
        action = "Created"
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceExistsException":
            raise
        # Secret already exists — update the value
        resp = client.put_secret_value(
            SecretId    = name,
            SecretString= secret_value,
        )
        action = "Updated"

    arn = resp.get("ARN") or client.describe_secret(SecretId=name)["ARN"]

    print(f"{action} secret: {name}")
    print(f"ARN: {arn}")
    print()
    print("Use this ARN in register_pipeline.py --secret-arn:")
    print(f"  {arn}")


def main():
    p = argparse.ArgumentParser(
        description="Register Oracle JDBC credentials in Secrets Manager."
    )
    p.add_argument("--name",     required=True, help="Secret name, e.g. oracle-erp (oracle- prefix added if missing)")
    p.add_argument("--username", required=True, help="Oracle username")
    p.add_argument("--password", required=True, help="Oracle password")
    p.add_argument("--host",     required=True, help="Oracle hostname or IP")
    p.add_argument("--port",     default=1521,  type=int, help="Oracle listener port (default: 1521)")
    p.add_argument("--service",  required=True, help="Oracle service name or SID")

    register(p.parse_args())


if __name__ == "__main__":
    main()
