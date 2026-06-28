// Bicep — FibreOps demo infrastructure
// Provisions the Azure services required for the end-to-end demo.
// Foundry project, model deployment, identity and RBAC are intentionally
// outside this template — provision those in the Foundry portal once and
// then set AZURE_AI_PROJECT_ENDPOINT in the local .env.

targetScope = 'resourceGroup'

@description('Short prefix used for all resource names (3-8 lowercase chars).')
@minLength(3)
@maxLength(8)
param namePrefix string = 'fbreops'

@description('Azure region for deployment.')
param location string = resourceGroup().location

@description('Event Hubs SKU (Standard required for Microsoft Entra auth).')
@allowed([ 'Basic', 'Standard', 'Premium' ])
param eventHubSku string = 'Standard'

@description('Container image reference (registry/repo:tag) for the FibreOps NOC service. AZD overrides this after the first build.')
param containerImageName string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Foundry project endpoint (https://<acct>.services.ai.azure.com/api/projects/<proj>). Empty = run with local backend.')
param azureAiProjectEndpoint string = ''

@description('Foundry model deployment name.')
param azureAiModelDeployment string = 'gpt-4.1-mini'

@description('Agent backend resolution: auto | hosted | foundry | local')
@allowed([ 'auto', 'hosted', 'foundry', 'local' ])
param fibreopsAgentBackend string = 'hosted'

@description('Azure Voice Live realtime endpoint. Either an https://<region>.api.cognitive.microsoft.com host or a fully-formed wss:// URL. Empty = browser falls back to text-only outbox.')
param azureVoiceLiveEndpoint string = ''

@description('Azure Voice Live API key (Speech / AI Services resource key). Required when azureVoiceLiveEndpoint is set.')
@secure()
param azureVoiceLiveApiKey string = ''

@description('Default voice for one-shot TTS, e.g. en-GB-RyanNeural. Empty = library default.')
param azureVoiceLiveVoice string = 'en-GB-RyanNeural'

@description('Voice Live managed model name (e.g. gpt-4o-mini, gpt-realtime, gpt-4.1-mini). NOT an Azure OpenAI deployment name — Voice Live models are fully managed and must not be deployed.')
param azureVoiceLiveModel string = 'gpt-4o-mini'

@description('Published Foundry agent id used by the duplex "Talk to agent" mic session. Empty disables the mic button.')
param azureVoiceLiveAgentId string = ''

@description('Voice Live realtime API version query parameter.')
param azureVoiceLiveApiVersion string = '2025-05-01-preview'

@description('Provision a Speech / AI Services account in this resource group for Voice Live. Set false to bring your own.')
param provisionVoiceLive bool = true

@description('Override region for the Speech / AI Services account. Empty = same as deployment location. Voice Live preview availability varies by region (e.g. eastus2, swedencentral, westus2).')
param voiceLiveLocation string = ''

@description('SKU for the Speech / AI Services account.')
@allowed([ 'S0', 'F0' ])
param voiceLiveSku string = 'S0'

@description('Tag value AZD uses to map this resource to the named service in azure.yaml.')
param serviceName string = 'fibreops-noc'

var suffix = uniqueString(resourceGroup().id, namePrefix)
var ehNamespaceName = toLower('${namePrefix}-ehns-${suffix}')
var ehName = 'fibre-signals'
var lawName = '${namePrefix}-law-${suffix}'
var kvName = toLower('${namePrefix}kv${substring(suffix, 0, 6)}')
var appiName = '${namePrefix}-appi-${suffix}'
var acrName = toLower('${namePrefix}acr${substring(suffix, 0, 8)}')
var planName = '${namePrefix}-plan-${suffix}'
var webName = toLower('${namePrefix}-noc-${substring(suffix, 0, 6)}')
var voiceLiveAccountName = toLower('${namePrefix}-aisvc-${substring(suffix, 0, 6)}')
var voiceLiveRegion = empty(voiceLiveLocation) ? location : voiceLiveLocation

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: lawName
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: appiName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: law.id
  }
}

resource ehNamespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: ehNamespaceName
  location: location
  sku: { name: eventHubSku, tier: eventHubSku, capacity: 1 }
  properties: {
    minimumTlsVersion: '1.2'
    disableLocalAuth: true
  }
}

resource eh 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: ehNamespace
  name: ehName
  properties: {
    messageRetentionInDays: 1
    partitionCount: 2
  }
}

// Azure AI Services (multi-service) account — provides Voice Live realtime
// Speech endpoint. Conditional so operators can BYO via azureVoiceLiveEndpoint
// (e.g. an existing AIFoundry / Speech account in another resource group).
resource voiceLiveAccount 'Microsoft.CognitiveServices/accounts@2024-10-01' = if (provisionVoiceLive) {
  name: voiceLiveAccountName
  location: voiceLiveRegion
  kind: 'AIServices'
  sku: { name: voiceLiveSku }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: voiceLiveAccountName
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: false
  }
}

// Resolved endpoint + key. Param override wins over the provisioned account.
var resolvedVoiceLiveEndpoint = !empty(azureVoiceLiveEndpoint)
  ? azureVoiceLiveEndpoint
  : (provisionVoiceLive ? voiceLiveAccount.properties.endpoint : '')
var resolvedVoiceLiveKey = !empty(azureVoiceLiveApiKey)
  ? azureVoiceLiveApiKey
  : (provisionVoiceLive ? voiceLiveAccount.listKeys().key1 : '')

resource kv 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: kvName
  location: location
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

// Voice Live API key is stored as a KV secret (rather than a plain app setting)
// so it can be rotated independently and is referenced from App Service via
// a Key Vault reference. The web MI gets `Key Vault Secrets User` from the
// post-deploy scripts/grant-mi-roles.ps1 script. The resource is conditional
// so deployments that omit the key (text-only fallback path) still succeed.
var voiceLiveSecretName = 'AZURE-VOICE-LIVE-API-KEY'
var hasVoiceLiveKey = provisionVoiceLive || !empty(azureVoiceLiveApiKey)
resource voiceLiveKeySecret 'Microsoft.KeyVault/vaults/secrets@2024-11-01' = if (hasVoiceLiveKey) {
  parent: kv
  name: voiceLiveSecretName
  properties: {
    value: resolvedVoiceLiveKey
    contentType: 'Azure Voice Live (Speech) resource key'
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: {
    // Admin user is enabled because the deploying principal lacks
    // Microsoft.Authorization/roleAssignments/write and therefore cannot
    // grant AcrPull to the Container App MI. The post-deploy script in
    // scripts/grant-mi-roles.ps1 lets an admin tighten this later.
    adminUserEnabled: true
    publicNetworkAccess: 'Enabled'
    anonymousPullEnabled: false
  }
}

resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: {
    name: 'B1'
    tier: 'Basic'
  }
  properties: {
    reserved: true
  }
}

resource web 'Microsoft.Web/sites@2024-04-01' = {
  name: webName
  location: location
  kind: 'app,linux,container'
  tags: {
    'azd-service-name': serviceName
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    siteConfig: {
      linuxFxVersion: 'DOCKER|${containerImageName}'
      alwaysOn: true
      ftpsState: 'Disabled'
      http20Enabled: true
      webSocketsEnabled: true
      minTlsVersion: '1.2'
      healthCheckPath: '/healthz'
      acrUseManagedIdentityCreds: false
      appSettings: [
        // Container registry pull credentials. ACR admin user is enabled
        // because the deploying principal lacks roleAssignments/write so we
        // cannot grant AcrPull to the site MI. scripts/grant-mi-roles.ps1
        // lets an admin tighten this later.
        { name: 'DOCKER_REGISTRY_SERVER_URL', value: 'https://${acr.properties.loginServer}' }
        { name: 'DOCKER_REGISTRY_SERVER_USERNAME', value: acr.listCredentials().username }
        { name: 'DOCKER_REGISTRY_SERVER_PASSWORD', value: acr.listCredentials().passwords[0].value }
        { name: 'WEBSITES_PORT', value: '8800' }
        { name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE', value: 'false' }
        { name: 'FIBREOPS_AGENT_BACKEND', value: fibreopsAgentBackend }
        { name: 'AZURE_AI_PROJECT_ENDPOINT', value: azureAiProjectEndpoint }
        { name: 'AZURE_AI_MODEL_DEPLOYMENT', value: azureAiModelDeployment }
        { name: 'EVENT_HUB_FQDN', value: '${ehNamespace.name}.servicebus.windows.net' }
        { name: 'EVENT_HUB_NAME', value: eh.name }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appi.properties.ConnectionString }
        { name: 'APPINSIGHTS_INSTRUMENTATIONKEY', value: appi.properties.InstrumentationKey }
        { name: 'FIBREOPS_UI_HOST', value: '0.0.0.0' }
        { name: 'FIBREOPS_UI_PORT', value: '8800' }
        { name: 'KEY_VAULT_NAME', value: kv.name }
        { name: 'AZURE_VOICE_LIVE_ENDPOINT', value: resolvedVoiceLiveEndpoint }
        { name: 'AZURE_VOICE_LIVE_API_KEY', value: hasVoiceLiveKey ? '@Microsoft.KeyVault(VaultName=${kv.name};SecretName=${voiceLiveSecretName})' : '' }
        { name: 'AZURE_VOICE_LIVE_VOICE', value: azureVoiceLiveVoice }
        { name: 'AZURE_VOICE_LIVE_MODEL', value: azureVoiceLiveModel }
        { name: 'AZURE_VOICE_LIVE_AGENT_ID', value: azureVoiceLiveAgentId }
        { name: 'AZURE_VOICE_LIVE_API_VERSION', value: azureVoiceLiveApiVersion }
      ]
    }
  }
}

// Built-in role definitions (kept for documentation + the post-deploy script)
// scripts/grant-mi-roles.ps1 uses these IDs. Role assignments are NOT created
// here because the deploying principal lacks Microsoft.Authorization/*/write
// in this subscription. An admin runs the script once after `azd up`.
//
//   Event Hubs Data Owner   f526a384-b230-433a-b45c-95f59c4a2dec
//   Key Vault Secrets User  4633458b-17de-408a-b874-0445c86b69e6
//   AcrPull                 7f951dda-4ed3-4680-a7ca-43fe172d538d
//   Azure AI User           53ca6127-db72-4b80-b1b0-d745d6d5456d (on Foundry account)
//   Cognitive Services User a97b65f3-24c7-4388-baec-2e87135dc908 (on Voice Live AI Services account)

output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = resourceGroup().name
output AZURE_APP_SERVICE_PLAN_NAME string = plan.name
output AZURE_APP_SERVICE_NAME string = web.name
output AZURE_APP_SERVICE_PRINCIPAL_ID string = web.identity.principalId
output AZURE_APP_SERVICE_HOSTNAME string = web.properties.defaultHostName
output AZURE_APP_SERVICE_URL string = 'https://${web.properties.defaultHostName}'
output M365_ACTION_BASE_URL string = 'https://${web.properties.defaultHostName}'
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output EVENT_HUB_FQDN string = '${ehNamespace.name}.servicebus.windows.net'
output EVENT_HUB_NAME string = eh.name
output KEY_VAULT_NAME string = kv.name
output APPLICATIONINSIGHTS_CONNECTION_STRING string = appi.properties.ConnectionString
output AZURE_LOG_ANALYTICS_WORKSPACE_ID string = law.id
output AZURE_VOICE_LIVE_ACCOUNT_NAME string = provisionVoiceLive ? voiceLiveAccount.name : ''
output AZURE_VOICE_LIVE_ACCOUNT_ID string = provisionVoiceLive ? voiceLiveAccount.id : ''
output AZURE_VOICE_LIVE_ENDPOINT string = resolvedVoiceLiveEndpoint

