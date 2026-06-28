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

.PARAMETER FoundryProjectName
    Name of the Foundry project whose managed identity pulls the hosted-agent
    image. If omitted, it is derived from the /projects/<name> segment of
    AZURE_AI_PROJECT_ENDPOINT, or discovered via ARM when the account has a
    single project. Pass this explicitly when the account hosts several projects.

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
    [string]$FoundryResourceGroup = "",
    [string]$FoundryProjectName   = ""
)

$ErrorActionPreference = "Stop"

if (-not $FoundryAccountName) {
    $endpoint = $env:AZURE_AI_PROJECT_ENDPOINT
    if ($endpoint -match "https?://([^.]+)\.services\.ai\.azure\.com") {
        $FoundryAccountName = $matches[1]
        Write-Host "Derived FoundryAccountName from AZURE_AI_PROJECT_ENDPOINT: $FoundryAccountName" -ForegroundColor DarkGray
    }
    else {
        Write-Host "FoundryAccountName not supplied; Foundry role grants will be skipped." -ForegroundColor Yellow
    }
}

if ($FoundryAccountName -and -not $FoundryResourceGroup) {
    throw "FoundryResourceGroup is required when FoundryAccountName is set. Pass -FoundryResourceGroup <rg-name>."
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
$voiceLive   = az resource list -g $ResourceGroup --resource-type Microsoft.CognitiveServices/accounts --query "[?kind=='AIServices'] | [0].id" -o tsv
$searchSvc   = az resource list -g $ResourceGroup --resource-type Microsoft.Search/searchServices --query "[0].id" -o tsv
$foundry     = if ($FoundryAccountName) { az cognitiveservices account show -g $FoundryResourceGroup -n $FoundryAccountName --query id -o tsv } else { "" }

$grants = @(
    @{ Role = "Azure Event Hubs Data Owner";    Scope = $ehNamespace; Desc = "publish/consume fibre-signals" },
    @{ Role = "Key Vault Secrets User";          Scope = $keyVault;    Desc = "read optional secrets (incl. Voice Live key)" },
    @{ Role = "AcrPull";                          Scope = $registry;    Desc = "pull image (tighten away from admin creds)" }
)
if ($voiceLive) {
    $grants += @{ Role = "Cognitive Services User"; Scope = $voiceLive; Desc = "use Voice Live realtime Speech endpoint" }
}
if ($searchSvc) {
    $grants += @{ Role = "Search Index Data Reader"; Scope = $searchSvc; Desc = "retrieve from the Foundry IQ knowledge base" }
}
if ($foundry) {
    $grants += @{ Role = "Azure AI Developer";             Scope = $foundry; Desc = "invoke hosted Prompt Agents in Foundry Agent Service" }
    $grants += @{ Role = "Cognitive Services OpenAI User"; Scope = $foundry; Desc = "call the underlying model deployment" }
}

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

# Foundry IQ: the knowledge base MCP endpoint is reached over RBAC, so the
# search service must accept Entra tokens (not API key only) or agents get 403.
if ($searchSvc) {
    $searchName = ($searchSvc -split "/")[-1]
    Write-Host "Enabling RBAC (aadOrApiKey) auth on search service '$searchName' (Foundry IQ MCP)..." -ForegroundColor Cyan
    az search service update --name $searchName --resource-group $ResourceGroup --auth-options aadOrApiKey --aad-auth-failure-mode http403 2>$null | Out-Null
    Write-Host ($LASTEXITCODE -eq 0 ? "  OK" : "  FAILED (enable manually: az search service update --auth-options aadOrApiKey --aad-auth-failure-mode http403)") -ForegroundColor ($LASTEXITCODE -eq 0 ? "Green" : "Red")
}

# --- Hosted agent (containerised) image pulls ---
# A Foundry **hosted agent** runs your container in a per-session sandbox; the
# platform pulls the image using the Foundry project's managed identity, which
# needs AcrPull (Container Registry Repository Reader) on the registry. Grant it
# here so `python -m fibreops.demo deploy-hosted` can reach the image.
if ($foundry -and $registry) {
    Write-Host ""
    Write-Host "Granting Foundry project MI AcrPull on the registry (hosted-agent image pulls)..." -ForegroundColor Cyan
    # IMPORTANT: the platform pulls the hosted-agent image using the Foundry
    # *project's* managed identity -- which is a DIFFERENT principal from the
    # account's identity. Granting AcrPull to the account MI does NOT fix the
    # ImageError; the grant must target the project MI. Resolve the project name
    # in priority order: explicit -FoundryProjectName, the /projects/<name>
    # segment of AZURE_AI_PROJECT_ENDPOINT, then discover it via ARM (use the
    # only project under the account, if unambiguous).
    $foundryProjectName = $FoundryProjectName
    if (-not $foundryProjectName -and $env:AZURE_AI_PROJECT_ENDPOINT -match "/projects/([^/?]+)") {
        $foundryProjectName = $matches[1]
    }
    $subId = az account show --query id -o tsv
    if (-not $foundryProjectName) {
        $projectsUrl = "https://management.azure.com/subscriptions/$subId/resourceGroups/$FoundryResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccountName/projects?api-version=2025-06-01"
        $projectNames = az rest --method get --url $projectsUrl --query "value[].name" -o tsv 2>$null
        $projectList = @($projectNames -split "\r?\n" | Where-Object { $_ })
        if ($projectList.Count -eq 1) {
            # ARM returns names as '<account>/<project>'; keep the trailing segment.
            $foundryProjectName = ($projectList[0] -split "/")[-1]
            Write-Host "  discovered sole project under '$FoundryAccountName': $foundryProjectName" -ForegroundColor DarkGray
        }
        elseif ($projectList.Count -gt 1) {
            Write-Host "  Multiple projects under '$FoundryAccountName'; pass -FoundryProjectName <name> to target the right one:" -ForegroundColor Yellow
            $projectList | ForEach-Object { Write-Host "    - $(($_ -split '/')[-1])" -ForegroundColor Yellow }
        }
    }
    if ($foundryProjectName) {
        $projUrl = "https://management.azure.com/subscriptions/$subId/resourceGroups/$FoundryResourceGroup/providers/Microsoft.CognitiveServices/accounts/$FoundryAccountName/projects/$foundryProjectName?api-version=2025-06-01"
        $foundryPrincipalId = az rest --method get --url $projUrl --query "identity.principalId" -o tsv 2>$null
        Write-Host "  project '$foundryProjectName' MI principalId = $foundryPrincipalId" -ForegroundColor DarkGray
    }
    else {
        Write-Host "  Could not resolve a Foundry project name; falling back to the account MI (hosted-agent pulls may still fail)." -ForegroundColor Yellow
        $foundryPrincipalId = az cognitiveservices account show -g $FoundryResourceGroup -n $FoundryAccountName --query identity.principalId -o tsv
    }
    if (-not $foundryPrincipalId) {
        Write-Host "  Foundry project has no system-assigned identity; enable it then re-run, or grant AcrPull manually." -ForegroundColor Yellow
    }
    else {
        $existing = az role assignment list --assignee-object-id $foundryPrincipalId --assignee-principal-type ServicePrincipal --scope $registry --role "AcrPull" --query "[0].id" -o tsv 2>$null
        if ($existing) {
            Write-Host "  already granted -> $existing" -ForegroundColor Yellow
        }
        else {
            az role assignment create `
                --assignee-object-id $foundryPrincipalId `
                --assignee-principal-type ServicePrincipal `
                --role "AcrPull" `
                --scope $registry | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "  FAILED (need Microsoft.Authorization/roleAssignments/write on the registry)" -ForegroundColor Red
                # Delegated 'Foundry Owner' has an ABAC condition that only permits
                # assigning a fixed allow-list of Foundry/AI roles -- AcrPull is NOT
                # on it, so a normal project owner cannot grant this. Emit a ready-to-
                # paste command for an admin (Owner / User Access Administrator) to run.
                Write-Host "  -> Ask an admin (Owner / User Access Administrator) to run:" -ForegroundColor Yellow
                Write-Host ""
                Write-Host "     az role assignment create ``" -ForegroundColor White
                Write-Host "       --assignee-object-id $foundryPrincipalId ``" -ForegroundColor White
                Write-Host "       --assignee-principal-type ServicePrincipal ``" -ForegroundColor White
                Write-Host "       --role AcrPull ``" -ForegroundColor White
                Write-Host "       --scope $registry" -ForegroundColor White
                Write-Host ""
            } else {
                Write-Host "  OK" -ForegroundColor Green
            }
        }
        # Foundry IQ: the project MI also retrieves from the knowledge base when
        # the hosted agent reaches it through an MCP toolbox connection.
        if ($searchSvc) {
            $existsSearch = az role assignment list --assignee-object-id $foundryPrincipalId --assignee-principal-type ServicePrincipal --scope $searchSvc --role "Search Index Data Reader" --query "[0].id" -o tsv 2>$null
            if ($existsSearch) {
                Write-Host "  project MI already has Search Index Data Reader." -ForegroundColor Yellow
            }
            else {
                az role assignment create --assignee-object-id $foundryPrincipalId --assignee-principal-type ServicePrincipal --role "Search Index Data Reader" --scope $searchSvc | Out-Null
                Write-Host ($LASTEXITCODE -eq 0 ? "  project MI granted Search Index Data Reader." : "  FAILED to grant project MI Search Index Data Reader.") -ForegroundColor ($LASTEXITCODE -eq 0 ? "Green" : "Red")
            }
        }
    }
    Write-Host "  NOTE: the user running 'deploy-hosted' also needs 'Azure AI Project Manager' at project scope." -ForegroundColor DarkGray

    # Foundry MI pulls require the registry to accept ARM-issued (RBAC) tokens.
    # Enable the ARM-auth policy here so the project MI's AcrPull actually works.
    $armAuth = az acr config authentication-as-arm show --registry ($registry -split "/")[-1] --query "status" -o tsv 2>$null
    if ($armAuth -eq "enabled") {
        Write-Host "  registry ARM-auth policy already enabled." -ForegroundColor DarkGray
    }
    else {
        Write-Host "  Enabling registry ARM-auth policy (required for Foundry MI pulls)..." -ForegroundColor Cyan
        az acr config authentication-as-arm update --registry ($registry -split "/")[-1] --status enabled --query "status" -o tsv 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  FAILED to enable ARM-auth; run manually: az acr config authentication-as-arm update --registry <acr> --status enabled" -ForegroundColor Red
        } else {
            Write-Host "  OK" -ForegroundColor Green
        }
    }
}

Write-Host ""
Write-Host "Once all four grants are green, you can:" -ForegroundColor Cyan
Write-Host "  1. Wire AcrPull on the App Service:" -ForegroundColor Cyan
Write-Host "       az webapp config set -g $ResourceGroup -n $AppServiceName --generic-configurations '{\"acrUseManagedIdentityCreds\": true}'"
Write-Host "  2. Disable ACR admin user:  az acr update -n <acr-name> --admin-enabled false" -ForegroundColor Cyan
Write-Host "  3. Restart the App Service to pick up the new permissions:" -ForegroundColor Cyan
Write-Host "       az webapp restart -g $ResourceGroup -n $AppServiceName"
