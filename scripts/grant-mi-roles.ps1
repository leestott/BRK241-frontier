#requires -Version 7.0
<#
.SYNOPSIS
    Grants the FibreOps App Service managed identity the RBAC roles its
    workload needs. Must be run by a principal with Microsoft.Authorization/*
    permissions (Owner or User Access Administrator) on each target scope.

.DESCRIPTION
    The deployment bicep does NOT create these role assignments because the
    deploying account is a Contributor on the subscription (not an Owner).
    Run this script ONCE after `azd up` completes to wire managed-identity
    auth for Event Hubs, Key Vault, ACR, and the Foundry account.

.PARAMETER ResourceGroup
    Resource group containing the FibreOps deployment. Default: rg-fibreops-demo

.PARAMETER AppServiceName
    Name of the App Service (Linux container) hosting FibreOps. If omitted,
    the script discovers it from the resource group.

.PARAMETER FoundryAccountName
    Name of the Foundry Cognitive Services account that hosts the project.
    Required. Read from the AZURE_AI_PROJECT_ENDPOINT host (the leading
    subdomain before `.services.ai.azure.com`) if not supplied.

.PARAMETER FoundryResourceGroup
    Resource group containing the Foundry account. Required.

.EXAMPLE
    pwsh scripts/grant-mi-roles.ps1 `
      -FoundryAccountName my-foundry-account `
      -FoundryResourceGroup rg-my-foundry

.EXAMPLE
    # Reuse the AZURE_AI_PROJECT_ENDPOINT value from `azd env get-values`
    pwsh scripts/grant-mi-roles.ps1 -FoundryResourceGroup rg-my-foundry
#>
param(
    [string]$ResourceGroup        = "rg-fibreops-demo",
    [string]$AppServiceName       = "",
    [string]$FoundryAccountName   = "",
    [string]$FoundryResourceGroup = ""
)

$ErrorActionPreference = "Stop"

if (-not $FoundryAccountName) {
    $endpoint = $env:AZURE_AI_PROJECT_ENDPOINT
    if ($endpoint -match "https?://([^.]+)\.services\.ai\.azure\.com") {
        $FoundryAccountName = $matches[1]
        Write-Host "Derived FoundryAccountName from AZURE_AI_PROJECT_ENDPOINT: $FoundryAccountName" -ForegroundColor DarkGray
    }
    else {
        throw "FoundryAccountName not supplied and could not be derived from AZURE_AI_PROJECT_ENDPOINT. Pass -FoundryAccountName <name>."
    }
}

if (-not $FoundryResourceGroup) {
    throw "FoundryResourceGroup not supplied. Pass -FoundryResourceGroup <rg-name> (the RG containing the Foundry account)."
}

if (-not $AppServiceName) {
    Write-Host "Discovering App Service in $ResourceGroup..." -ForegroundColor Cyan
    $AppServiceName = az webapp list -g $ResourceGroup --query "[0].name" -o tsv
    if (-not $AppServiceName) {
        throw "No App Service found in $ResourceGroup. Pass -AppServiceName explicitly."
    }
}

Write-Host "Looking up App Service MI principal id for $AppServiceName..." -ForegroundColor Cyan
$principalId = az webapp show -g $ResourceGroup -n $AppServiceName --query identity.principalId -o tsv
if (-not $principalId) {
    throw "Could not retrieve managed identity principal id from $AppServiceName in $ResourceGroup. Has `azd up` finished?"
}
Write-Host "  principalId = $principalId" -ForegroundColor Green

$ehNamespace = az resource list -g $ResourceGroup --resource-type Microsoft.EventHub/namespaces --query "[0].id" -o tsv
$keyVault    = az resource list -g $ResourceGroup --resource-type Microsoft.KeyVault/vaults     --query "[0].id" -o tsv
$registry    = az resource list -g $ResourceGroup --resource-type Microsoft.ContainerRegistry/registries --query "[0].id" -o tsv
$foundry     = az cognitiveservices account show -g $FoundryResourceGroup -n $FoundryAccountName --query id -o tsv

$grants = @(
    @{ Role = "Azure Event Hubs Data Owner"; Scope = $ehNamespace; Desc = "publish/consume fibre-signals" },
    @{ Role = "Key Vault Secrets User";       Scope = $keyVault;    Desc = "read optional secrets" },
    @{ Role = "AcrPull";                       Scope = $registry;    Desc = "pull image (tighten away from admin creds)" },
    @{ Role = "Azure AI User";                 Scope = $foundry;     Desc = "invoke hosted Prompt Agents in Foundry Agent Service" }
)

foreach ($g in $grants) {
    Write-Host "Granting [$($g.Role)] -> $($g.Desc)" -ForegroundColor Cyan
    Write-Host "  scope: $($g.Scope)" -ForegroundColor DarkGray
    $existing = az role assignment list --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --scope $g.Scope --role $g.Role --query "[0].id" -o tsv 2>$null
    if ($existing) {
        Write-Host "  already granted -> $existing" -ForegroundColor Yellow
        continue
    }
    az role assignment create `
        --assignee-object-id $principalId `
        --assignee-principal-type ServicePrincipal `
        --role $g.Role `
        --scope $g.Scope | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAILED (you may not have Microsoft.Authorization/roleAssignments/write on this scope)" -ForegroundColor Red
    } else {
        Write-Host "  OK" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "Once all four grants are green, you can:" -ForegroundColor Cyan
Write-Host "  1. Wire AcrPull on the App Service:" -ForegroundColor Cyan
Write-Host "       az webapp config set -g $ResourceGroup -n $AppServiceName --generic-configurations '{\"acrUseManagedIdentityCreds\": true}'"
Write-Host "  2. Disable ACR admin user:  az acr update -n <acr-name> --admin-enabled false" -ForegroundColor Cyan
Write-Host "  3. Restart the App Service to pick up the new permissions:" -ForegroundColor Cyan
Write-Host "       az webapp restart -g $ResourceGroup -n $AppServiceName"
