from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
import json, os
from dotenv import load_dotenv

load_dotenv('C:/Users/leestott/BRK241-frontier/.azure/fibreops-demo/.env')
endpoint = os.environ.get('AZURE_AI_PROJECT_ENDPOINT') or os.environ.get('FOUNDRY_PROJECT_ENDPOINT')
print(f'Endpoint: {endpoint}')

client = AIProjectClient(endpoint=endpoint, credential=DefaultAzureCredential(), allow_preview=True)
try:
    agents = client.agents.list()
    # agents might be a list or an object with a .data attribute
    agent_list = agents.data if hasattr(agents, 'data') else agents
    for a in agent_list:
        print(f'Agent Name: {a.name}, ID: {a.id}')
        try:
            versions = client.agents.list_versions(agent_name=a.name)
            ver_items = versions.data if hasattr(versions, 'data') else versions
            for v in ver_items:
                print(f'  - Version: {getattr(v, "version", "N/A")}, ID: {getattr(v, "id", "N/A")}')
        except Exception as ve:
            print(f'  - Could not list versions for {a.name}: {ve}')
except Exception as e:
    print(f'Error listing agents: {e}')
finally:
    client.close()
