#requires -Version 7.0
<#
.SYNOPSIS
    azd `postdeploy` orchestration for FibreOps — finishes the *complete*
    solution after the NOC console image is deployed to App Service.

.DESCRIPTION
    `azd up` provisions infra (incl. the Voice Live AI Services account) and
    deploys the NOC console container. Two pieces of the solution have no native
    azd host type and are completed here:

      1. Publish the three role Prompt Agents to Microsoft Foundry Agent Service
         (fibreops-incident-analysis / -netops-coordinator / -field-dispatch).
         The default `hosted` agent backend binds to these, so the NOC console
         cannot produce runs until they exist. Runs by default; skip with
         FIBREOPS_SKIP_PUBLISH=true.

      2. Deploy the containerised hosted agent (the single /responses agent).
         OFF by default; enable with FIBREOPS_DEPLOY_HOSTED=true.

    Failures fail `azd up` rather than reporting a deployed but unusable NOC.
    RBAC for the App Service / Foundry project managed identities is granted
    once by scripts/grant-mi-roles.ps1.

    Reads AZURE_AI_PROJECT_ENDPOINT / AZURE_AI_MODEL_DEPLOYMENT /
    AZURE_CONTAINER_REGISTRY_NAME / AZURE_RESOURCE_GROUP from the environment
    (azd injects the azd env values when it runs the hook).
#>
param(
    [string]$ProjectEndpoint = $env:AZURE_AI_PROJECT_ENDPOINT,
    [string]$ModelDeployment = $env:AZURE_AI_MODEL_DEPLOYMENT,
    [string]$RegistryName    = $env:AZURE_CONTAINER_REGISTRY_NAME,
    [string]$ResourceGroup   = $env:AZURE_RESOURCE_GROUP,
    [string]$FoundryAccountName = $env:AZURE_FOUNDRY_ACCOUNT_NAME,
    [string]$FoundryResourceGroup = $env:AZURE_RESOURCE_GROUP,
    [string]$FoundryProjectName = $env:AZURE_FOUNDRY_PROJECT_NAME
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# Resolve a Python interpreter: prefer the repo venv, else system python.
$venvPython = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = Join-Path $repoRoot ".venv/bin/python"   # Linux/macOS agents
}
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

if (-not $ProjectEndpoint) {
    throw "AZURE_AI_PROJECT_ENDPOINT is required for agent publishing and hosted-agent deployment."
}

# Make the Foundry config visible to the fibreops CLI.
$env:AZURE_AI_PROJECT_ENDPOINT = $ProjectEndpoint
if ($ModelDeployment) { $env:AZURE_AI_MODEL_DEPLOYMENT = $ModelDeployment }

# Grant the web and Foundry project identities access before publishing agents.
& "$PSScriptRoot/grant-mi-roles.ps1" -ResourceGroup $ResourceGroup `
    -AppServiceName $env:AZURE_APP_SERVICE_NAME `
    -FoundryAccountName $FoundryAccountName `
    -FoundryResourceGroup $FoundryResourceGroup `
    -FoundryProjectName $FoundryProjectName

# --- 0. Provision the Foundry IQ knowledge base (Azure AI Search) ---
# azd output AZURE_SEARCH_SERVICE_NAME / AZURE_SEARCH_ENDPOINT identify the
# search service stood up by the bicep. Seed the index + knowledge base from the
# FibreOps SOPs + topology. Skip with FIBREOPS_SKIP_FOUNDRY_IQ=true.
$searchName = $env:AZURE_SEARCH_SERVICE_NAME
$searchEndpoint = $env:AZURE_SEARCH_ENDPOINT
if ($env:FIBREOPS_SKIP_FOUNDRY_IQ -eq 'true') {
    Write-Host "FIBREOPS_SKIP_FOUNDRY_IQ=true -> skipping Foundry IQ knowledge base provisioning." -ForegroundColor DarkGray
}
elseif ($searchName -and $searchEndpoint) {
    Write-Host "Provisioning the Foundry IQ knowledge base in '$searchName' ..." -ForegroundColor Cyan
    $adminKey = az search admin-key show --service-name $searchName --resource-group $ResourceGroup --query primaryKey -o tsv 2>$null
    if ($adminKey) { $env:SEARCH_ADMIN_KEY = $adminKey }
    & $python "$repoRoot/scripts/provision_foundry_iq.py" --endpoint $searchEndpoint
    $env:SEARCH_ADMIN_KEY = $null
    if ($LASTEXITCODE -ne 0) {
        throw "Foundry IQ provisioning failed. Re-run scripts/provision_foundry_iq.py --endpoint $searchEndpoint"
    }
    else {
        Write-Host "  Foundry IQ knowledge base ready." -ForegroundColor Green
        # Create the project connection to the KB MCP endpoint so the hosted
        # incident-analysis agent grounds via knowledge_base_retrieve.
        $kb = $env:FOUNDRY_IQ_KNOWLEDGE_BASE; if (-not $kb) { $kb = "fibreops-knowledge-base" }
        $conn = $env:FOUNDRY_IQ_MCP_CONNECTION; if (-not $conn) { $conn = "fibreops-kb-mcp" }
        if ($FoundryAccountName -and $FoundryResourceGroup -and $FoundryProjectName) {
            & $python "$repoRoot/scripts/connect_foundry_iq.py" `
                --subscription-id $env:AZURE_SUBSCRIPTION_ID `
                --foundry-account $FoundryAccountName --foundry-resource-group $FoundryResourceGroup `
                --project-name $FoundryProjectName --search-endpoint $searchEndpoint `
                --knowledge-base $kb --connection-name $conn
            if ($LASTEXITCODE -ne 0) { throw "Failed to connect Foundry IQ to project $FoundryProjectName." }
            $env:FOUNDRY_IQ_SEARCH_ENDPOINT = $searchEndpoint
            $env:FOUNDRY_IQ_KNOWLEDGE_BASE = $kb
            $env:FOUNDRY_IQ_MCP_CONNECTION = $conn
        } else {
            Write-Host "  Pass -FoundryAccountName/-FoundryResourceGroup/-FoundryProjectName to also create the MCP connection." -ForegroundColor DarkGray
        }
    }
}
else {
    Write-Host "No Azure AI Search service found (AZURE_SEARCH_ENDPOINT unset) -> skipping Foundry IQ." -ForegroundColor DarkGray
}

# --- 1. Publish the three role Prompt Agents (default ON) ---
if ($env:FIBREOPS_SKIP_PUBLISH -eq 'true') {
    Write-Host "FIBREOPS_SKIP_PUBLISH=true -> skipping Prompt Agent publish." -ForegroundColor DarkGray
}
else {
    Write-Host "Publishing the three role Prompt Agents to Foundry (hosted backend needs these)..." -ForegroundColor Cyan
    & $python -m fibreops.demo publish
    if ($LASTEXITCODE -ne 0) {
        throw "Prompt Agent publish failed. Ensure Azure AI Project Manager permissions and re-run $python -m fibreops.demo publish."
    }
    else {
        Write-Host "  Prompt Agents published." -ForegroundColor Green
        # Keep published versions in App Service configuration, not in Git or
        # the image. App Service restarts the container after the setting changes.
        $registryPath = Join-Path $repoRoot "state/foundry_agents.json"
        if (-not (Test-Path $registryPath)) {
            throw "Publishing succeeded but agent registry was not written to $registryPath"
        }
        $appServiceName = $env:AZURE_APP_SERVICE_NAME
        if (-not $ResourceGroup -or -not $appServiceName) {
            throw "AZURE_RESOURCE_GROUP and AZURE_APP_SERVICE_NAME are required to configure published agent versions."
        }
        $publishedAgents = (Get-Content $registryPath -Raw | ConvertFrom-Json | ConvertTo-Json -Compress -Depth 10)
        $settingsFile = [System.IO.Path]::Combine(
            [System.IO.Path]::GetTempPath(), [System.IO.Path]::GetRandomFileName() + ".json"
        )
        try {
            $settingsJson = @{ FIBREOPS_PUBLISHED_AGENTS = $publishedAgents } | ConvertTo-Json -Compress
            [System.IO.File]::WriteAllText($settingsFile, $settingsJson, [System.Text.UTF8Encoding]::new($false))
            az webapp config appsettings set --resource-group $ResourceGroup --name $appServiceName `
                --settings "@$settingsFile" --output none
            if ($LASTEXITCODE -ne 0) {
                throw "Failed to configure FIBREOPS_PUBLISHED_AGENTS on App Service $appServiceName"
            }
        }
        finally {
            Remove-Item -LiteralPath $settingsFile -Force
        }
        Write-Host "  Published agent versions configured on App Service." -ForegroundColor Green
    }
}

# --- 2. Deploy the containerised hosted agent (default OFF) ---
if ($env:FIBREOPS_DEPLOY_HOSTED -eq 'true') {
    Write-Host "FIBREOPS_DEPLOY_HOSTED=true -> deploying hosted agent to Foundry Agent Service..." -ForegroundColor Cyan
    & "$PSScriptRoot/deploy-hosted-agent.ps1" -RegistryName $RegistryName -ResourceGroup $ResourceGroup
    if ($LASTEXITCODE -ne 0) { throw "Hosted-agent deploy failed. Check Foundry project MI AcrPull in scripts/grant-mi-roles.ps1." }
}
else {
    Write-Host "Skipping hosted-agent deploy. Enable with: azd env set FIBREOPS_DEPLOY_HOSTED true" -ForegroundColor DarkGray
}

# Voice Agents Preview requires azure-ai-projects>=2.7 while Agent Framework
# currently requires <2.7. Publish from a separate, ignored environment.
if ($env:FIBREOPS_PUBLISH_VOICE -eq 'true') {
    $voiceName = $env:AZURE_VOICE_AGENT_NAME
    if (-not $voiceName) { $voiceName = 'fibreops-noc-voice' }
    $env:AZURE_VOICE_AGENT_NAME = $voiceName
    $voiceVenv = Join-Path $repoRoot ".venv-voice"
    $voicePython = if ($IsWindows) {
        Join-Path $voiceVenv "Scripts/python.exe"
    } else {
        Join-Path $voiceVenv "bin/python"
    }
    if (-not (Test-Path $voicePython)) {
        & $python -m venv $voiceVenv
        if ($LASTEXITCODE -ne 0) { throw "Unable to create isolated voice publisher environment." }
    }
    & $voicePython -m pip install -r "$repoRoot/requirements-voice.txt" --quiet
    if ($LASTEXITCODE -ne 0) { throw "Unable to install isolated voice publisher dependencies." }
    $publishedVoice = @(& $voicePython "$repoRoot/scripts/publish_voice_agent.py")
    if ($LASTEXITCODE -ne 0 -or -not $publishedVoice) {
        throw "Foundry Voice Agents Preview publishing failed."
    }
    $voiceVersion = [string]$publishedVoice[-1]
    if (-not $ResourceGroup -or -not $env:AZURE_APP_SERVICE_NAME) {
        throw "AZURE_RESOURCE_GROUP and AZURE_APP_SERVICE_NAME are required for voice-agent configuration."
    }
    az webapp config appsettings set --resource-group $ResourceGroup --name $env:AZURE_APP_SERVICE_NAME `
        --settings "AZURE_VOICE_AGENT_NAME=$voiceName" "AZURE_VOICE_AGENT_VERSION=$voiceVersion" --output none
    if ($LASTEXITCODE -ne 0) { throw "Unable to configure voice agent on App Service." }
    azd env set AZURE_VOICE_AGENT_NAME $voiceName
    if ($LASTEXITCODE -ne 0) { throw "Unable to save local voice agent name." }
    azd env set AZURE_VOICE_AGENT_VERSION $voiceVersion
    if ($LASTEXITCODE -ne 0) { throw "Unable to save local voice agent version." }
    Write-Host "Foundry voice agent $voiceName version $voiceVersion is configured." -ForegroundColor Green
}

Write-Host ""
Write-Host "Reminder: an Owner / User Access Administrator must run scripts/grant-mi-roles.ps1 once" -ForegroundColor DarkGray
Write-Host "to grant the App Service + Foundry project managed identities their workload roles." -ForegroundColor DarkGray
