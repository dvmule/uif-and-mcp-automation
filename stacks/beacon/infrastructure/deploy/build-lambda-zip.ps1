# Build + stage the beacon-lambdas zip (the Step Functions executors + the
# scheduled starter). First deploy: run BEFORE the full terraform apply (the
# functions reference this S3 key). Updates: -UpdateFunctions refreshes the
# running Lambdas.
#
# Since UIFPE-438 the zip is pure code — no vendored dependencies. SFTP
# delivery rides Transfer Family connectors via boto3 (in the Lambda
# runtime), so a zip built on Windows is identical to the CI-built one.
param(
    [string]$Env = "dev",
    [string]$Profile = "gps-sandbox",
    [string]$Region = "eu-west-1",
    [switch]$UpdateFunctions
)
$ErrorActionPreference = "Stop"
# deploy/ -> infrastructure/ -> domain root
$domainRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$bucket = "uif-beacon-$Env-data"
$zip = Join-Path $env:TEMP "beacon-lambdas.zip"

python -c "
import zipfile, pathlib
src = pathlib.Path(r'$domainRoot') / 'src' / 'apps' / 'beacon_export'
with zipfile.ZipFile(r'$zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for p in src.rglob('*.py'):
        z.write(p, 'beacon_export/' + str(p.relative_to(src)).replace('\\', '/'))
print('zipped', len(z.namelist()) if False else 'ok')
"
aws s3 cp $zip "s3://$bucket/code/beacon-lambdas.zip" --profile $Profile --region $Region

if ($UpdateFunctions) {
    foreach ($fn in @("deliver", "record-run", "emit-event")) {
        aws lambda update-function-code --function-name "uif-beacon-$Env-$fn" `
            --s3-bucket $bucket --s3-key code/beacon-lambdas.zip `
            --profile $Profile --region $Region | Out-Null
        Write-Host "updated uif-beacon-$Env-$fn"
    }
}
