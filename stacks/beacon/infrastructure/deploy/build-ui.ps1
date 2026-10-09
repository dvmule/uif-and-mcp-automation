# Build + deploy the React UI: vite build -> S3 sync -> CloudFront invalidation.
param(
    [string]$Env = "dev",
    [string]$Profile = "gps-sandbox",
    [string]$Region = "eu-west-1"
)
$ErrorActionPreference = "Stop"
# deploy/ -> infrastructure/ -> domain root -> ui/
$ui = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "ui"
$bucket = "uif-beacon-$Env-ui"

npm --prefix $ui run build
aws s3 sync (Join-Path $ui "dist") "s3://$bucket/" --delete --profile $Profile --region $Region

# invalidate the distribution that serves this bucket
$dists = aws cloudfront list-distributions --profile $Profile | ConvertFrom-Json
$dist = $dists.DistributionList.Items | Where-Object {
    $_.Origins.Items.DomainName -like "$bucket.s3*" } | Select-Object -First 1
if ($dist) {
    aws cloudfront create-invalidation --distribution-id $dist.Id --paths "/*" --profile $Profile | Out-Null
    Write-Host "deployed + invalidated: https://$($dist.DomainName)"
} else {
    Write-Warning "no CloudFront distribution found for $bucket (first apply not done yet?)"
}
