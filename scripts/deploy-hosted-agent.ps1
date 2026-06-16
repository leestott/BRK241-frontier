#requires -Version 7.0
<#
.SYNOPSIS
    Build, push, and deploy the FibreOps **containerised hosted agent** to
    Foundry Agent Service (V1Preview).

.DESCRIPTION
    Packages the "Outage Response Agent System" (src/fibreops/agents/hosted_app.py)
    as an x86_64 container, pushes it to Azure Container Registry, then registers
    it as a hosted-agent version in your Foundry project via
    `python -m fibreops.demo deploy-hosted` (which builds a HostedAgentDefinition
    with the azure-ai-projects SDK and polls until the sandbox is active).

    The image is built **in ACR** with `az acr build --platform linux/amd64`, so
    a local Docker daemon is NOT required. The hosting platform pulls the image
    using the Foundry project's managed identity, which must hold
    *Container Registry Repository Reader* (AcrPull) on the registry — run
    scripts/grant-mi-roles.ps1 with -FoundryAccountName to grant it.

.PARAMETER RegistryName
    Azure Container Registry name (without .azurecr.io). If omitted, the script
    discovers the first ACR in -ResourceGroup.

.PARAMETER ResourceGroup
    Resource group containing the ACR. Default: rg-fibreops-demo

.PARAMETER ImageRepository
    Image repository name. Default: fibreops-outage-response

.PARAMETER Tag
    Image tag. Default: a UTC timestamp (avoid ":latest" for hosted agents).

.PARAMETER NoWait
    Register the version but return without polling for the active status.

.PARAMETER SkipBuild
    Skip the ACR build/push and deploy the existing image:tag as-is.

.EXAMPLE
    pwsh scripts/deploy-hosted-agent.ps1 -RegistryName fbreopsacr12345678 -ResourceGroup rg-fibreops-demo

.EXAMPLE
    # Deploy a pre-built image without rebuilding
    pwsh scripts/deploy-hosted-agent.ps1 -RegistryName fbreopsacr -Tag v3 -SkipBuild
#>
param(
    [string]$RegistryName    = "",
    [string]$ResourceGroup   = "rg-fibreops-demo",
    [string]$ImageRepository = "fibreops-outage-response",
    [string]$Tag             = "",
    [switch]$NoWait,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dockerfile = Join-Path $repoRoot "src/fibreops/agents/Dockerfile.hosted"
if (-not (Test-Path $dockerfile)) {
    throw "Hosted-agent Dockerfile not found at $dockerfile"
}

if (-not $Tag) {
    $Tag = (Get-Date -AsUTC -Format "yyyyMMddHHmmss")
}

if (-not $RegistryName) {
    Write-Host "Discovering Azure Container Registry in $ResourceGroup..." -ForegroundColor Cyan
    $RegistryName = az acr list -g $ResourceGroup --query "[0].name" -o tsv
    if (-not $RegistryName) {
        throw "No ACR found in $ResourceGroup. Pass -RegistryName explicitly."
    }
}

$loginServer = az acr show -n $RegistryName --query loginServer -o tsv
if (-not $loginServer) {
    throw "Could not resolve login server for ACR '$RegistryName'."
}
$image = "$loginServer/$($ImageRepository):$Tag"
Write-Host "Target image: $image" -ForegroundColor Green

if (-not $SkipBuild) {
    Write-Host "Building x86_64 image in ACR (no local Docker needed)..." -ForegroundColor Cyan
    az acr build `
        --registry $RegistryName `
        --platform linux/amd64 `
        --image "$($ImageRepository):$Tag" `
        --file $dockerfile `
        $repoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "az acr build failed."
    }
    Write-Host "  pushed $image" -ForegroundColor Green
}
else {
    Write-Host "SkipBuild set — deploying existing $image" -ForegroundColor Yellow
}

# Resolve the Python interpreter (prefer the repo venv).
$venvPython = Join-Path $repoRoot ".venv/Scripts/python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

Write-Host "Registering hosted-agent version in Foundry..." -ForegroundColor Cyan
$env:FIBREOPS_HOSTED_IMAGE = $image
$deployArgs = @("-m", "fibreops.demo", "deploy-hosted", "--image", $image)
if ($NoWait) { $deployArgs += "--no-wait" }

Push-Location $repoRoot
try {
    & $python @deployArgs
    $deployExit = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($deployExit -ne 0) {
    Write-Host "Hosted-agent deploy reported a non-zero exit code ($deployExit)." -ForegroundColor Red
    Write-Host "Common causes:" -ForegroundColor Yellow
    Write-Host "  * The deploying user needs 'Azure AI Project Manager' at project scope." -ForegroundColor Yellow
    Write-Host "  * The Foundry project MI needs 'Container Registry Repository Reader' on $RegistryName." -ForegroundColor Yellow
    Write-Host "    -> pwsh scripts/grant-mi-roles.ps1 -FoundryAccountName <acct> -FoundryResourceGroup <rg>" -ForegroundColor Yellow
    exit $deployExit
}

Write-Host ""
Write-Host "Done. Invoke the agent with:" -ForegroundColor Cyan
Write-Host "  project.get_openai_client(agent_name='$ImageRepository').responses.create(input='...')" -ForegroundColor Gray
