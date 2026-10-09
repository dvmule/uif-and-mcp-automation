param(
    [string]$Profile = "gps-sandbox",
    [string]$Region = "us-east-1",
    [string]$ExpectedAccount = "061206535585",
    [string]$StackName = "product-change-pipeline-test"
)

$ErrorActionPreference = "Stop"

Write-Host "Checking AWS identity for profile '$Profile'..."
$identity = aws sts get-caller-identity --profile $Profile --output json | ConvertFrom-Json
Write-Host "Account: $($identity.Account)"
Write-Host "Arn:    $($identity.Arn)"

if ($identity.Account -ne $ExpectedAccount) {
    throw "Expected account $ExpectedAccount for profile $Profile, got $($identity.Account)"
}

# Client-rule generation retired (2026-09-17): subscriptions live in the
# uif-event-{env}-subscription-configs table.

# sam must run from the project root (template.yaml / samconfig.toml live
# there), regardless of where this script is invoked from.
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    Write-Host "Building SAM application..."
    sam build
    if ($LASTEXITCODE -ne 0) { throw "sam build failed" }

    Write-Host "Deploying stack to $Region..."
    sam deploy --profile $Profile --region $Region
    if ($LASTEXITCODE -ne 0) { throw "sam deploy failed" }

    # The Event Fabric Processor's script is not packaged by SAM - upload it
    # to the ScriptLocation the Glue job points at.
    $bucket = aws cloudformation describe-stacks --stack-name $StackName --profile $Profile --region $Region `
        --query "Stacks[0].Outputs[?OutputKey=='DataBucketName'].OutputValue" --output text
    if (-not $bucket) { throw "Could not resolve DataBucketName output" }
    Write-Host "Uploading Event Fabric Processor script to s3://$bucket/glue/..."
    aws s3 cp "src\glue\event_fabric_processor.py" "s3://$bucket/glue/event_fabric_processor.py" --profile $Profile --region $Region
    if ($LASTEXITCODE -ne 0) { throw "Glue script upload failed" }
}
finally {
    Pop-Location
}

Write-Host "Deployment complete. Next: .\test-upload.ps1 -Profile $Profile -RunId <run id in the gold table>"
