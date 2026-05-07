import os
import sys
import logging
import base64
import requests
import markdown
import json
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv(Path(os.getcwd()) / '.env')

URL_BASE = os.environ.get('ATLASSIAN_URL', 'tasteslikegood.atlassian.net')
EMAIL = os.environ.get('ATLASSIAN_EMAIL')
TOKEN = os.environ.get('ATLASSIAN_API_TOKEN')

if EMAIL and TOKEN:
    AUTH_STR = f"{EMAIL}:{TOKEN}"
    AUTH_B64 = base64.b64encode(AUTH_STR.encode('utf-8')).decode('utf-8')
    HEADERS = {
        "Authorization": f"Basic {AUTH_B64}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
else:
    HEADERS = {}
    logger.warning("Atlassian credentials missing from .env")

SPACE_ID = "11042818"  # TLG space ID
PARENT_PAGE_ID = "11796481"  # Project Documentation page ID

# Initialize FastMCP server
mcp = FastMCP("PM Daemon")

WATCHED_FILES = [
    "plan.md",
    "roadmap.md",
    "planning_notes.md",
    "design-plan.md",
    "SCRUM_BOOTSTRAP_AND_BOARD_PLAN.md",
    "SPRINT_0_PLAN.md",
    "ATLASSIAN_PM_LINK.md",
]

class PMFileEventHandler(FileSystemEventHandler):
    def __init__(self, workspace_dir):
        self.workspace_dir = Path(workspace_dir)
        super().__init__()

    def on_modified(self, event):
        if event.is_directory:
            return
        
        filepath = Path(event.src_path)
        if filepath.name in WATCHED_FILES:
            logger.info(f"Detected change in {filepath.name}. Triggering PM sync...")
            self.sync_to_confluence(filepath)
            
    def sync_to_confluence(self, filepath):
        if not HEADERS:
            logger.error("Cannot sync to confluence: No auth headers.")
            return
        
        title = f"v0.2 {filepath.name.replace('.md', '').replace('_', ' ').title()}"
        if filepath.name == "roadmap.md":
            title = "v0.2 Project Roadmap"
        elif filepath.name == "plan.md":
            title = "v0.2 Execution Plan"
        elif filepath.name == "planning_notes.md":
            title = "v0.2 Planning Session Review & Notes"
        elif filepath.name == "design-plan.md":
            title = "v0.2 Design Implementation Plan"
        elif filepath.name == "SCRUM_BOOTSTRAP_AND_BOARD_PLAN.md":
            title = "v0.2 Scrum Bootstrap & Board Plan"
        elif filepath.name == "SPRINT_0_PLAN.md":
            title = "v0.2 Sprint 0 Plan"
        elif filepath.name == "ATLASSIAN_PM_LINK.md":
            title = "v0.2 Atlassian PM Link"

        content = filepath.read_text()
        logger.info(f"Syncing content of {filepath.name} to Confluence: {title}")
        
        html_content = markdown.markdown(content, extensions=['fenced_code', 'tables'])
        
        # 1. Search for existing page
        search_url = f"https://{URL_BASE}/wiki/api/v2/spaces/{SPACE_ID}/pages?title={requests.utils.quote(title)}"
        page_id = None
        version = 1
        
        try:
            resp = requests.get(search_url, headers=HEADERS)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    page_id = results[0]['id']
                    # Get current version
                    page_resp = requests.get(f"https://{URL_BASE}/wiki/api/v2/pages/{page_id}", headers=HEADERS)
                    if page_resp.status_code == 200:
                        version = page_resp.json().get('version', {}).get('number', 0) + 1
        except Exception as e:
            logger.error(f"Error checking page existence: {e}")

        payload = {
            "spaceId": SPACE_ID,
            "status": "current",
            "title": title,
            "parentId": PARENT_PAGE_ID,
            "body": {
                "representation": "storage",
                "value": html_content
            }
        }
        
        if page_id:
            # Update existing page
            url = f"https://{URL_BASE}/wiki/api/v2/pages/{page_id}"
            payload["id"] = page_id
            payload["version"] = {"number": version, "message": "Updated by PM Daemon"}
            req_func = requests.put
            logger.info(f"Updating existing Confluence page: {page_id} to version {version}")
        else:
            # Create new page
            url = f"https://{URL_BASE}/wiki/api/v2/pages"
            req_func = requests.post
            logger.info(f"Creating new Confluence page")

        try:
            response = req_func(url, headers=HEADERS, json=payload)
            if response.status_code in [200, 201]:
                logger.info(f"Successfully synced: {title}")
            else:
                logger.error(f"Failed to sync {title}: {response.status_code} {response.text}")
        except Exception as e:
            logger.error(f"Error syncing page {title}: {e}")

@mcp.tool()
def get_project_status() -> str:
    """Get the current project status and active sprint data from PM notes.
    Call this tool at the beginning of a session to brief the agent on what to do.
    """
    workspace_dir = Path(os.getcwd())
    status = []
    for filename in WATCHED_FILES:
        matches = [workspace_dir / filename, *workspace_dir.rglob(filename)]
        seen_paths = set()
        for filepath in matches:
            if not filepath.exists() or filepath in seen_paths:
                continue
            seen_paths.add(filepath)
            content = filepath.read_text()
            label = str(filepath.relative_to(workspace_dir))
            status.append(f"--- {label} ---\n{content[:1500]}" + ("..." if len(content) > 1500 else ""))
    
    if not status:
        return "No local planning files found in the current workspace."
    
    return "CURRENT PM BRIEFING:\n" + "\n".join(status)

@mcp.tool()
def sync_pm_documents() -> str:
    """Force a sync of the local PM documents to Jira/Confluence.
    Call this tool when you want to immediately update Jira/Confluence based on local file changes.
    """
    workspace_dir = Path(os.getcwd())
    handler = PMFileEventHandler(workspace_dir)
    synced = []
    for filename in WATCHED_FILES:
        matches = [workspace_dir / filename, *workspace_dir.rglob(filename)]
        seen_paths = set()
        for filepath in matches:
            if not filepath.exists() or filepath in seen_paths:
                continue
            seen_paths.add(filepath)
            handler.sync_to_confluence(filepath)
            synced.append(str(filepath.relative_to(workspace_dir)))
            
    return f"Sync triggered successfully for {', '.join(synced)}. The PM Daemon has updated the documents in Atlassian."

@mcp.tool()
def create_epic_from_roadmap(epic_name: str, description: str) -> str:
    """Create a new Epic in Jira based on roadmap planning."""
    if not HEADERS:
        return "Error: Atlassian credentials missing."
        
    url = f"https://{URL_BASE}/rest/api/3/issue"
    payload = {
        "fields": {
            "project": {"key": "KAN"},
            "summary": epic_name,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {"type": "text", "text": description}
                        ]
                    }
                ]
            },
            "issuetype": {"name": "Epic"}
        }
    }
    
    try:
        response = requests.post(url, headers=HEADERS, json=payload)
        if response.status_code == 201:
            issue_key = response.json().get('key')
            return f"Successfully created Epic: {issue_key}"
        else:
            return f"Failed to create Epic: {response.status_code} {response.text}"
    except Exception as e:
        return f"Error creating Epic: {e}"

def start_watcher(workspace_dir: str):
    logger.info(f"Starting file watcher on workspace: {workspace_dir}")
    event_handler = PMFileEventHandler(workspace_dir)
    observer = Observer()
    observer.schedule(event_handler, workspace_dir, recursive=True)
    observer.start()
    return observer

if __name__ == "__main__":
    import time
    workspace_dir = os.getcwd()
    observer = start_watcher(workspace_dir)
    watch_only = "--watch-only" in sys.argv
    try:
        if watch_only:
            logger.info("Running in --watch-only mode (no MCP transport). Press Ctrl+C to stop.")
            while True:
                time.sleep(60)
        else:
            mcp.run()
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join()
