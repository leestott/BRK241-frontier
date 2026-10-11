#requires -Version 7.0
<#
.SYNOPSIS
    Verifies that Azure CLI, AZD, permissions, and Foundry access target the
    intended tenant and subscription before deployment.
#>
param(
    [Parameter(Mandatory)]
    [string]$ExpectedTenantId,
    [Parameter(Mandatory)]
    [string]$ExpectedSubscriptionId,
    [string]$ExpectedUser = "",
    [string]$Environment = $env:AZURE_ENV_NAME,
    [switch]$SkipFoundryProbe
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Assert-Equal([string]$Label, [string]$Actual, [string]$Expected) {
    if (-not $Actual) { throw "$Label could not be resolved." }
    if ($Actual -ne $Expected) {
        throw "$Label mismatch. Expected '$Expected'; active value is '$Actual'."
    }
    Write-Host "  OK $Label = $Actual" -ForegroundColor Green
}

Write-Host "Checking Azure CLI identity..." -ForegroundColor Cyan
$account = az account show --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Azure CLI is not signed in. Run az login --tenant $ExpectedTenantId." }
Assert-Equal "tenant" $account.tenantId $ExpectedTenantId
Assert-Equal "subscription" $account.id $ExpectedSubscriptionId
if ($ExpectedUser) { Assert-Equal "user" $account.user.name $ExpectedUser }

Write-Host "Checking AZD identity and selected environment..." -ForegroundColor Cyan
azd auth login --check-status | Out-Null
if ($LASTEXITCODE -ne 0) { throw "AZD is not signed in. Run azd auth login --tenant-id $ExpectedTenantId." }
$azdArgs = @("env", "get-values")
if ($Environment) { $azdArgs += @("--environment", $Environment) }
$azdValues = & azd @azdArgs
if ($LASTEXITCODE -ne 0) { throw "Unable to read the selected AZD environment." }
$azdSubscription = (($azdValues | Select-String '^AZURE_SUBSCRIPTION_ID=').Line -replace '^AZURE_SUBSCRIPTION_ID="?|"$', '')
Assert-Equal "AZD subscription" $azdSubscription $ExpectedSubscriptionId

Write-Host "Checking deployment permissions..." -ForegroundColor Cyan
$userObjectId = az ad signed-in-user show --query id --output tsv
if ($LASTEXITCODE -ne 0 -or -not $userObjectId) { throw "Unable to resolve the signed-in user's object ID." }
$subscriptionScope = "/subscriptions/$ExpectedSubscriptionId"
$roles = @(az role assignment list --assignee-object-id $userObjectId --all --include-groups `
    --query "[].roleDefinitionName" --output tsv)
if ($LASTEXITCODE -ne 0) { throw "Unable to list role assignments at $subscriptionScope." }
$canAssign = $roles -contains "Owner" -or $roles -contains "User Access Administrator" -or $roles -contains "Role Based Access Control Administrator"
if ($canAssign) {
    Write-Host "  OK role-assignment permission is available through an administrative role." -ForegroundColor Green
}
else {
    Write-Warning "No standard role-assignment administrator role was detected. A custom role may work, but postdeploy RBAC can fail without Microsoft.Authorization/roleAssignments/write."
}

if (-not $SkipFoundryProbe) {
    Write-Host "Checking Foundry data-plane access..." -ForegroundColor Cyan
    $python = Join-Path $repoRoot ".venv/Scripts/python.exe"
    if (-not (Test-Path $python)) { $python = "python" }
    $probeArgs = @("$repoRoot/check_agents.py")
    if ($Environment) { $probeArgs += @("--environment", $Environment) }
    & $python @probeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Foundry agent listing failed. Verify Foundry Agent Consumer/Foundry Project Manager access and RBAC propagation."
    }
}

Write-Host ""
Write-Host "Azure CLI and AZD are aligned." -ForegroundColor Green
Write-Warning "VS Code Azure/Foundry MCP authentication is independent. In VS Code, sign the MCP server into tenant $ExpectedTenantId before using MCP evaluation or AppLens tools."
