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

    Both steps are best-effort: failures print guidance but never fail `azd up`
    (the hook sets continueOnError). RBAC for the App Service / Foundry project
    managed identities is still granted once by scripts/grant-mi-roles.ps1.

    Reads AZURE_AI_PROJECT_ENDPOINT / AZURE_AI_MODEL_DEPLOYMENT /
    AZURE_CONTAINER_REGISTRY_NAME / AZURE_RESOURCE_GROUP from the environment
    (azd injects the azd env values when it runs the hook).
#>
param(
    [string]$ProjectEndpoint = $env:AZURE_AI_PROJECT_ENDPOINT,
    [string]$ModelDeployment = $env:AZURE_AI_MODEL_DEPLOYMENT,
    [string]$RegistryName    = $env:AZURE_CONTAINER_REGISTRY_NAME,
    [string]$ResourceGroup   = $env:AZURE_RESOURCE_GROUP
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
    Write-Host "AZURE_AI_PROJECT_ENDPOINT not set — skipping agent publish + hosted-agent deploy." -ForegroundColor Yellow
    Write-Host "  Set it with: azd env set AZURE_AI_PROJECT_ENDPOINT <project-endpoint>" -ForegroundColor DarkGray
    return
}

# Make the Foundry config visible to the fibreops CLI.
$env:AZURE_AI_PROJECT_ENDPOINT = $ProjectEndpoint
if ($ModelDeployment) { $env:AZURE_AI_MODEL_DEPLOYMENT = $ModelDeployment }

# --- 1. Publish the three role Prompt Agents (default ON) ---
if ($env:FIBREOPS_SKIP_PUBLISH -eq 'true') {
    Write-Host "FIBREOPS_SKIP_PUBLISH=true -> skipping Prompt Agent publish." -ForegroundColor DarkGray
}
else {
    Write-Host "Publishing the three role Prompt Agents to Foundry (hosted backend needs these)..." -ForegroundColor Cyan
    & $python -m fibreops.demo publish
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Prompt Agent publish failed. The NOC console will return 500 on inject until this succeeds." -ForegroundColor Red
        Write-Host "  Ensure you hold 'Azure AI Project Manager' at project scope, then re-run:" -ForegroundColor Yellow
        Write-Host "    $python -m fibreops.demo publish" -ForegroundColor White
    }
    else {
        Write-Host "  Prompt Agents published." -ForegroundColor Green
    }
}

# --- 2. Deploy the containerised hosted agent (default OFF) ---
if ($env:FIBREOPS_DEPLOY_HOSTED -eq 'true') {
    Write-Host "FIBREOPS_DEPLOY_HOSTED=true -> deploying hosted agent to Foundry Agent Service..." -ForegroundColor Cyan
    & "$PSScriptRoot/deploy-hosted-agent.ps1" -RegistryName $RegistryName -ResourceGroup $ResourceGroup
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Hosted-agent deploy failed (often a missing Foundry project MI AcrPull). See scripts/grant-mi-roles.ps1." -ForegroundColor Red
    }
}
else {
    Write-Host "Skipping hosted-agent deploy. Enable with: azd env set FIBREOPS_DEPLOY_HOSTED true" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "Reminder: an Owner / User Access Administrator must run scripts/grant-mi-roles.ps1 once" -ForegroundColor DarkGray
Write-Host "to grant the App Service + Foundry project managed identities their workload roles." -ForegroundColor DarkGray
