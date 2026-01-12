from pydantic import BaseModel, HttpUrl

class MoodleConnectRequest(BaseModel):
    moodle_url: HttpUrl
    token: str

class MoodleConnectResponse(BaseModel):
    connected: bool
    message: str
    site_name: str | None = None
    moodle_username: str | None = None
    moodle_version: str | None = None
