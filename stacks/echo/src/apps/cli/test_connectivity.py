#!/usr/bin/env python3
"""
VPC connectivity test for the ingestion framework.
Launches a temporary Lambda function inside the target subnet/SG and
runs TCP + DNS checks from inside the VPC — no bastion host needed.

Tests:
  1. Resolve Oracle hostname from within VPC DNS
  2. TCP connect to Oracle on the configured port
  3. AWS service reachability (DynamoDB, S3, Secrets Manager) via VPC endpoints

Usage:
    python3 scripts/test_connectivity.py \
        --secret-name oracle-dev \
        --subnet-id   subnet-0ed517553ab77ba42 \
        --sg-id       sg-06a116c1c86b8f5af \
        --region      eu-west-1

Requirements: boto3, AWS credentials active
"""

import argparse
import base64
import boto3
import json
import time
import zipfile
import io
import sys
from pathlib import Path

# Standalone CLIs get no conftest, so the libs path is set explicitly.
_LIBS = Path(__file__).resolve().parents[2] / "libs"
if str(_LIBS) not in sys.path:
    sys.path.insert(0, str(_LIBS))

import stack_naming as sn  # noqa: E402

REGION = "eu-west-1"

# Inline Lambda that runs connectivity checks from inside the VPC
PROBE_CODE = '''
import json
import socket
import urllib.request
import os

def handler(event, context):
    host    = event["host"]
    port    = int(event["port"])
    region  = event.get("region", "eu-west-1")
    results = []

    # ── 1. DNS resolution ────────────────────────────────────────────
    try:
        ip = socket.gethostbyname(host)
        results.append({"check": f"DNS resolve {host}", "status": "PASS", "detail": ip})
    except Exception as e:
        results.append({"check": f"DNS resolve {host}", "status": "FAIL", "detail": str(e)})
        ip = None

    # ── 2. TCP connect to Oracle ──────────────────────────────────────
    try:
        s = socket.create_connection((host, port), timeout=10)
        s.close()
        results.append({"check": f"TCP {host}:{port}", "status": "PASS", "detail": "connection established"})
    except Exception as e:
        results.append({"check": f"TCP {host}:{port}", "status": "FAIL", "detail": str(e)})

    # ── 3. DynamoDB VPC endpoint ──────────────────────────────────────
    try:
        import boto3
        boto3.client("dynamodb", region_name=region).list_tables(Limit=1)
        results.append({"check": "DynamoDB reachable", "status": "PASS", "detail": "via VPC endpoint or internet"})
    except Exception as e:
        results.append({"check": "DynamoDB reachable", "status": "FAIL", "detail": str(e)})

    # ── 4. S3 VPC endpoint ────────────────────────────────────────────
    try:
        import boto3
        boto3.client("s3", region_name=region).list_buckets()
        results.append({"check": "S3 reachable", "status": "PASS", "detail": "via VPC endpoint or internet"})
    except Exception as e:
        results.append({"check": "S3 reachable", "status": "FAIL", "detail": str(e)})

    # ── 5. Secrets Manager VPC endpoint ──────────────────────────────
    try:
        import boto3
        boto3.client("secretsmanager", region_name=region).list_secrets(MaxResults=1)
        results.append({"check": "Secrets Manager reachable", "status": "PASS", "detail": "via VPC endpoint or internet"})
    except Exception as e:
        results.append({"check": "Secrets Manager reachable", "status": "FAIL", "detail": str(e)})

    return results
'''


def make_zip(code: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("probe.py", code)
    return buf.getvalue()


def get_oracle_details(secret_name: str, region: str) -> tuple[str, int]:
    client = boto3.client("secretsmanager", region_name=region)
    secret = json.loads(
        client.get_secret_value(SecretId=secret_name)["SecretString"]
    )
    host = secret.get("host", "")
    port = int(secret.get("port", 1536))
    print(f"  Oracle host : {host}")
    print(f"  Oracle port : {port}")
    return host, port


def get_lambda_role(region: str) -> str:
    """Reuse the existing lambda execution role."""
    iam = boto3.client("iam", region_name=region)
    role_name = sn.load("dev").resource("lambda-role")
    arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
    print(f"  Using role  : {arn}")
    return arn


def deploy_probe(subnet_id: str, sg_id: str, role_arn: str, region: str) -> str:
    fn_name = f"ingestion-connectivity-probe-{int(time.time())}"
    client  = boto3.client("lambda", region_name=region)

    print(f"\n  Deploying probe Lambda: {fn_name} ...")
    client.create_function(
        FunctionName  = fn_name,
        Runtime       = "python3.11",
        Role          = role_arn,
        Handler       = "probe.handler",
        Code          = {"ZipFile": make_zip(PROBE_CODE)},
        Timeout       = 30,
        MemorySize    = 128,
        VpcConfig     = {
            "SubnetIds":        [subnet_id],
            "SecurityGroupIds": [sg_id],
        },
    )

    # Wait for Active state
    for _ in range(20):
        state = client.get_function_configuration(FunctionName=fn_name)["State"]
        if state == "Active":
            break
        print(f"    waiting... state={state}")
        time.sleep(3)

    print(f"  Probe ready.")
    return fn_name


def run_probe(fn_name: str, host: str, port: int, region: str) -> list:
    client  = boto3.client("lambda", region_name=region)
    payload = json.dumps({"host": host, "port": port, "region": region}).encode()

    print(f"\n  Invoking probe from inside VPC ...")
    resp     = client.invoke(FunctionName=fn_name, Payload=payload)
    fn_error = resp.get("FunctionError")
    results  = json.loads(resp["Payload"].read())

    if fn_error:
        print(f"  Lambda error: {results}")
        return []
    return results


def delete_probe(fn_name: str, region: str):
    try:
        boto3.client("lambda", region_name=region).delete_function(FunctionName=fn_name)
        print(f"\n  Probe Lambda deleted.")
    except Exception:
        print(f"\n  Warning: could not delete probe Lambda {fn_name} — delete manually.")


def print_results(results: list):
    sep = "─" * 55
    print(f"\n{sep}")
    print(f"  Connectivity Results")
    print(sep)
    passed = failed = 0
    for r in results:
        icon = "✓" if r["status"] == "PASS" else "✗"
        print(f"  {icon}  {r['check']}")
        print(f"       {r['detail']}")
        if r["status"] == "PASS":
            passed += 1
        else:
            failed += 1
    print(sep)
    print(f"  {passed} passed  {failed} failed")
    print(sep)
    return failed


def main():
    # Box-drawing output cannot encode to cp1252, which is what Windows
    # gives a non-terminal stdout -- declare it, as sync_pipelines.py does.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    p = argparse.ArgumentParser(description="Test VPC connectivity to Oracle from inside the subnet.")
    p.add_argument("--secret-name", default="oracle-dev",                  help="Secrets Manager secret name")
    p.add_argument("--subnet-id",   default="subnet-0ed517553ab77ba42",    help="Subnet to deploy probe into")
    p.add_argument("--sg-id",       default="sg-06a116c1c86b8f5af",        help="Security group for probe Lambda")
    p.add_argument("--region",      default="eu-west-1")
    args = p.parse_args()

    print("─" * 55)
    print("  VPC Connectivity Probe")
    print("─" * 55)

    print("\n[1] Reading Oracle connection details from Secrets Manager...")
    host, port = get_oracle_details(args.secret_name, args.region)

    print("\n[2] Resolving Lambda execution role...")
    role_arn = get_lambda_role(args.region)

    fn_name = None
    try:
        print("\n[3] Deploying probe Lambda into VPC...")
        fn_name = deploy_probe(args.subnet_id, args.sg_id, role_arn, args.region)

        print("\n[4] Running checks from inside the VPC...")
        results = run_probe(fn_name, host, port, args.region)

        failed = print_results(results)

        if failed > 0:
            print("\n  Remediation hints:")
            for r in results:
                if r["status"] == "FAIL":
                    if "TCP" in r["check"]:
                        print(f"  • Add inbound rule on Oracle SG: TCP {port} from sg-id {args.sg_id}")
                        print(f"  • Confirm subnet {args.subnet_id} has route to Oracle host")
                    if "DNS" in r["check"]:
                        print(f"  • Oracle hostname may be private — check Route53 or /etc/hosts in VPC")
                    if "DynamoDB" in r["check"] or "S3" in r["check"] or "Secrets" in r["check"]:
                        print(f"  • Check VPC endpoints or NAT Gateway for {r['check']}")

        sys.exit(0 if failed == 0 else 1)

    finally:
        if fn_name:
            delete_probe(fn_name, args.region)


if __name__ == "__main__":
    main()
