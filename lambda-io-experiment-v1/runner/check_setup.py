from __future__ import annotations

import os
from pathlib import Path

import boto3
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

region = os.getenv("AWS_REGION", "ap-northeast-2")
function_names = [
    os.getenv("DISK_FUNCTION_NAME"),
    os.getenv("NETWORK_FUNCTION_NAME"),
]

sts = boto3.client("sts", region_name=region)
identity = sts.get_caller_identity()

print("AWS credentials: OK")
print(f"Account: {identity['Account']}")
print(f"ARN:     {identity['Arn']}")
print(f"Region:  {region}")

lambda_client = boto3.client("lambda", region_name=region)

for function_name in function_names:
    if not function_name:
        continue

    config = lambda_client.get_function_configuration(FunctionName=function_name)
    ephemeral = config.get("EphemeralStorage", {}).get("Size", 512)

    print()
    print(f"Function: {function_name}")
    print(f"  State:        {config.get('State')}")
    print(f"  Update:       {config.get('LastUpdateStatus')}")
    print(f"  Runtime:      {config.get('Runtime')}")
    print(f"  Architecture: {config.get('Architectures')}")
    print(f"  Handler:      {config.get('Handler')}")
    print(f"  Memory:       {config.get('MemorySize')} MB")
    print(f"  Timeout:      {config.get('Timeout')} sec")
    print(f"  /tmp:         {ephemeral} MB")
