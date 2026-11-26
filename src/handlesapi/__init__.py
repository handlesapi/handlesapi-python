"""Python client for the Handles API: public social media data for X, Instagram, TikTok, YouTube,
Telegram, Bluesky, Twitch, Kick and LinkedIn companies. https://handlesapi.com/docs"""
from ._core import DEFAULT_BASE_URL, VERSION, Credits, RateLimit, Response
from ._operations import OPERATIONS, Operation
from .client import AsyncHandlesApi, HandlesApi
from .errors import HandlesApiConnectionError, HandlesApiError

__version__ = VERSION

__all__ = [
    "AsyncHandlesApi",
    "Credits",
    "DEFAULT_BASE_URL",
    "HandlesApi",
    "HandlesApiConnectionError",
    "HandlesApiError",
    "OPERATIONS",
    "Operation",
    "RateLimit",
    "Response",
    "__version__",
]
