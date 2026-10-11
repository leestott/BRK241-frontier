#requires -Version 7.0
<#
.SYNOPSIS
    Creates or reuses the single-tenant Entra app used by App Service auth and
    stores deployment values in the selected ignored AZD environment.
#>
param(
    [Parameter(Mandatory)]
    [string]$ExpectedTenantId,
    [Parameter(Mandatory)]
    [string]$ExpectedSubscriptionId,
    [string]$Environment = $env:AZURE_ENV_NAME,
    [string]$DisplayName = "FibreOps NOC",
    [string]$RedirectHostname = ""
)

$ErrorActionPreference = "Stop"
$account = az account show --output json | ConvertFrom-Json
if ($account.tenantId -ne $ExpectedTenantId -or $account.id -ne $ExpectedSubscriptionId) {
    throw "Active Azure context does not match tenant $ExpectedTenantId and subscription $ExpectedSubscriptionId."
}

$envArgs = @()
if ($Environment) { $envArgs = @("--environment", $Environment) }
function Get-AzdValue([string]$Name) {
    $value = & azd env get-value $Name @envArgs 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return [string]$value
}
function Set-AzdValue([string]$Name, [string]$Value) {
    & azd env set $Name $Value @envArgs | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Unable to set $Name in the AZD environment." }
}

$appId = Get-AzdValue "FIBREOPS_AAD_CLIENT_ID"
$app = $null
if ($appId) {
    $appJson = az ad app show --id $appId --output json 2>$null
    if ($LASTEXITCODE -eq 0 -and $appJson) { $app = $appJson | ConvertFrom-Json }
    else { throw "FIBREOPS_AAD_CLIENT_ID '$appId' is not present in tenant $ExpectedTenantId." }
}
if (-not $app) {
    Write-Host "Creating single-tenant Entra application '$DisplayName'..." -ForegroundColor Cyan
    $app = az ad app create --display-name $DisplayName --sign-in-audience AzureADMyOrg --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $app.appId) { throw "Entra application creation failed." }
    $appId = $app.appId
    Set-AzdValue "FIBREOPS_AAD_CLIENT_ID" $appId
}
elseif ($app.signInAudience -ne "AzureADMyOrg") {
    throw "Existing application '$appId' is not single-tenant (signInAudience=$($app.signInAudience))."
}
else {
    Write-Host "Reusing Entra application '$appId' from tenant $ExpectedTenantId." -ForegroundColor Green
}

az ad app update --id $appId --enable-id-token-issuance true | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to enable ID-token issuance for '$appId'." }

if (-not $RedirectHostname) { $RedirectHostname = Get-AzdValue "AZURE_APP_SERVICE_HOSTNAME" }
if ($RedirectHostname) {
    $redirectUri = "https://$RedirectHostname/.auth/login/aad/callback"
    $currentUris = @(az ad app show --id $appId --query "web.redirectUris" --output tsv)
    if ($currentUris -notcontains $redirectUri) {
        $allUris = @($currentUris | Where-Object { $_ }) + $redirectUri
        az ad app update --id $appId --web-redirect-uris @allUris | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Unable to configure redirect URI '$redirectUri'." }
    }
    Write-Host "  Redirect URI ready: $redirectUri" -ForegroundColor Green
}
else {
    Write-Host "No App Service hostname exists yet; rerun this script after provisioning to add the callback URI." -ForegroundColor Yellow
}

if (-not (Get-AzdValue "FIBREOPS_AAD_CLIENT_SECRET")) {
    Write-Host "Creating a one-year Entra application credential in the ignored AZD environment..." -ForegroundColor Cyan
    $credential = az ad app credential reset --id $appId --append --years 1 --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $credential.password) { throw "Unable to create the Entra application credential." }
    Set-AzdValue "FIBREOPS_AAD_CLIENT_SECRET" $credential.password
    $credential.password = $null
}
else {
    Write-Host "Existing AZD-held Entra credential retained." -ForegroundColor Green
}

$allowedUser = Get-AzdValue "FIBREOPS_ALLOWED_USER_OBJECT_ID"
if (-not $allowedUser) {
    $allowedUser = az ad signed-in-user show --query id --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $allowedUser) { throw "Unable to resolve the initial operator object ID." }
    Set-AzdValue "FIBREOPS_ALLOWED_USER_OBJECT_ID" $allowedUser
}
Write-Host "Entra bootstrap complete for app $appId." -ForegroundColor Green
