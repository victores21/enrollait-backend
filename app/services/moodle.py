import httpx
from typing import Any, Dict

class MoodleError(Exception):
    pass

class MoodleClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def call(self, wsfunction: str, **params) -> Any:
        url = f"{self.base_url}/webservice/rest/server.php"
        payload: Dict[str, Any] = {
            "wstoken": self.token,
            "moodlewsrestformat": "json",
            "wsfunction": wsfunction,
            **params,
        }

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, data=payload)
            resp.raise_for_status()
            data = resp.json()

        # Moodle errors often come back as JSON with "exception"
        if isinstance(data, dict) and data.get("exception"):
            raise MoodleError(data.get("message") or "Unknown Moodle error")

        return data

    async def test_connection(self) -> Dict[str, Any]:
        data = await self.call("core_webservice_get_site_info")
        # Example fields: sitename, username, userid, release, version, etc.
        return data
