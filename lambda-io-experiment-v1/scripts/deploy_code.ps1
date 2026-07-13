$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    throw ".env 파일이 없습니다. 먼저 Copy-Item .env.example .env 를 실행하고 값을 입력하세요."
}

Get-Content ".env" | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
}

if (-not $env:DISK_FUNCTION_NAME) {
    throw "DISK_FUNCTION_NAME이 .env에 없습니다."
}
if (-not $env:NETWORK_FUNCTION_NAME) {
    throw "NETWORK_FUNCTION_NAME이 .env에 없습니다."
}

aws lambda update-function-code `
    --region $env:AWS_REGION `
    --function-name $env:DISK_FUNCTION_NAME `
    --zip-file fileb://deployment/disk_lambda.zip

aws lambda wait function-updated-v2 `
    --region $env:AWS_REGION `
    --function-name $env:DISK_FUNCTION_NAME

aws lambda update-function-code `
    --region $env:AWS_REGION `
    --function-name $env:NETWORK_FUNCTION_NAME `
    --zip-file fileb://deployment/network_lambda.zip

aws lambda wait function-updated-v2 `
    --region $env:AWS_REGION `
    --function-name $env:NETWORK_FUNCTION_NAME

Write-Host "두 Lambda 함수 코드 업로드 완료"
