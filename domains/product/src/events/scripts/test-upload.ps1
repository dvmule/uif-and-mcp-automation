param(
    [string]$Profile = "gps-sandbox",
    [string]$Region = "us-east-1",
    [string]$StackName = "product-change-pipeline-test",
    # Must be a run id that exists in the gold table's _gold_run_id column.
    [Parameter(Mandatory = $true)][string]$RunId
)

$ErrorActionPreference = "Stop"
$awsArgs = @("--region", $Region)
if ($Profile) { $awsArgs += @("--profile", $Profile) }

$outputs = aws cloudformation describe-stacks @awsArgs `
    --stack-name $StackName --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
$bucket = ($outputs | Where-Object { $_.OutputKey -eq "DataBucketName" }).OutputValue
$functionName = ($outputs | Where-Object { $_.OutputKey -eq "LambdaFunctionName" }).OutputValue
$glueJob = ($outputs | Where-Object { $_.OutputKey -eq "EventFabricProcessorJobName" }).OutputValue
$runTable = ($outputs | Where-Object { $_.OutputKey -eq "RunRegistryTableName" }).OutputValue

if (-not $bucket -or -not $functionName -or -not $glueJob) {
    throw "Could not resolve stack outputs from $StackName"
}

Write-Host "Bucket: $bucket | Lambda: $functionName | Glue job: $glueJob | Run table: $runTable"

# Single source of truth: the domain's registered mapping in src/schemas/
# (the sample-data copy was removed - it had silently diverged).
$mappingPath = Join-Path $PSScriptRoot "..\..\schemas\mapping\product\v1\product.mapping.json"
1product.mapping.json"
Write-Host "Uploading gold attribute mapping (the Event Fabric Processor's channel rule source)..."
aws s3 cp $mappingPath "s3://$bucket/config/product.mapping.json" @awsArgs

# Invoke the thin-trigger Lambda: it starts the Event Fabric Processor.
# Payload goes via file:// - Windows PowerShell 5.1 strips embedded quotes
# when passing JSON strings to native executables.
$payloadFile = Join-Path $env:TEMP "invoke-payload.json"
@{ run_id = $RunId; changedBy = "test-invoke"; } | ConvertTo-Json -Compress |
    Out-File -FilePath $payloadFile -Encoding ascii
Write-Host ""
Write-Host "Invoking Lambda with run_id=$RunId (starts the Event Fabric Processor)..."
$outFile = Join-Path $env:TEMP "invoke-result.json"
aws lambda invoke `
    --function-name $functionName `
    --cli-binary-format raw-in-base64-out `
    --payload "fileb://$payloadFile" `
    @awsArgs `
    $outFile | Out-Host
$response = Get-Content $outFile | ConvertFrom-Json
$response | ConvertTo-Json | Out-Host
$jobRunId = $response.jobRunId
if (-not $jobRunId) { throw "Lambda did not return a jobRunId - see its logs" }

Write-Host ""
Write-Host "Waiting for Glue job run $jobRunId..."
do {
    Start-Sleep -Seconds 10
    $state = aws glue get-job-run --job-name $glueJob --run-id $jobRunId @awsArgs `
        --query "JobRun.JobRunState" --output text
    Write-Host "  $state"
} while ($state -in @("STARTING", "RUNNING", "WAITING"))

Write-Host ""
Write-Host "Run registry record:"
aws dynamodb get-item --table-name $runTable --key "{\`"run_id\`":{\`"S\`":\`"$RunId\`"}}" @awsArgs --output json | Out-Host

if ($state -ne "SUCCEEDED") {
    aws glue get-job-run --job-name $glueJob --run-id $jobRunId @awsArgs --query "JobRun.ErrorMessage" --output text | Out-Host
    throw "Glue job run finished $state"
}

Write-Host ""
Write-Host "Check client destinations: poll a client SQS queue"
Write-Host "  aws sqs receive-message --queue-url <queue url> --max-number-of-messages 10"
Write-Host "or read a client Kinesis stream with the console Data Viewer."
Write-Host "Done."
