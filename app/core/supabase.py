# app/core/supabase.py
from __future__ import annotations

import os
import inspect
from typing import Optional, Union

from fastapi import UploadFile
from supabase import create_client, Client

# ClientOptions exists in some versions, but the signature differs by version.
try:
    from supabase import ClientOptions  # type: ignore
except Exception:
    ClientOptions = None  # type: ignore


SUPABASE_URL = (os.getenv("SUPABASE_URL", "") or "").strip()
SUPABASE_SERVICE_ROLE_KEY = (os.getenv("SUPABASE_SERVICE_ROLE_KEY", "") or "").strip()
PRODUCT_IMAGES_BUCKET = (os.getenv("PRODUCT_IMAGES_BUCKET", "product-images") or "product-images").strip()

_supabase: Client | None = None


def _normalize_supabase_url(url: str) -> str:
    # For create_client, base project url should usually be without a trailing slash.
    return (url or "").strip().rstrip("/")


def _looks_like_jwt(value: str) -> bool:
    v = (value or "").strip()
    return v.startswith("eyJ") and v.count(".") >= 2


def _build_options_if_supported(base_url: str):
    """
    Creates ClientOptions only when available and only with params supported by this version.
    We do NOT assume storage_url exists (your error shows it does not).
    """
    if ClientOptions is None:
        return None

    try:
        sig = inspect.signature(ClientOptions.__init__)
        params = set(sig.parameters.keys())
    except Exception:
        # If inspection fails, safest is to not pass options at all.
        return None

    kwargs = {}

    # Many versions support schema/headers; keep minimal.
    # Only add keys if this version supports them.
    if "schema" in params:
        kwargs["schema"] = "public"

    # Some versions may support "auto_refresh_token", "persist_session" etc.
    # We avoid setting them unless needed.

    # Your version does NOT support storage_url, so we do not set it unless present.
    # (This avoids SyncClientOptions.__init__ unexpected keyword errors.)
    if "storage_url" in params:
        kwargs["storage_url"] = f"{base_url}/storage/v1/"  # trailing slash
    if "postgrest_url" in params:
        kwargs["postgrest_url"] = f"{base_url}/rest/v1/"   # trailing slash

    # If we ended up with no kwargs, skip options.
    if not kwargs:
        return None

    try:
        return ClientOptions(**kwargs)  # type: ignore
    except Exception:
        return None


def _client() -> Client:
    global _supabase

    if _supabase is not None:
        return _supabase

    url = _normalize_supabase_url(SUPABASE_URL)
    key = (SUPABASE_SERVICE_ROLE_KEY or "").strip()

    if not url:
        raise RuntimeError("SUPABASE_URL is missing")
    if not key:
        raise RuntimeError("SUPABASE_SERVICE_ROLE_KEY is missing")
    if not _looks_like_jwt(key):
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY does not look like a Supabase JWT (expected something starting with 'eyJ...'). "
            "Use the service_role key from Supabase Dashboard → Project Settings → API."
        )

    opts = _build_options_if_supported(url)
    if opts is not None:
        _supabase = create_client(url, key, options=opts)  # type: ignore
    else:
        _supabase = create_client(url, key)

    return _supabase


def upload_product_image(
    file_or_bytes: Union[UploadFile, bytes],
    path: str,
    content_type: Optional[str] = None,
) -> dict[str, str]:
    """
    Uploads to Supabase Storage and returns:
      { "path": "...", "public_url": "https://..." }

    NOTE:
    - If bucket is public, get_public_url returns a usable URL.
    - If bucket is private, you must use signed URLs instead.
    """
    sb = _client()
    bucket = sb.storage.from_(PRODUCT_IMAGES_BUCKET)

    if isinstance(file_or_bytes, UploadFile):
        data = file_or_bytes.file.read()
        ct = content_type or file_or_bytes.content_type or "application/octet-stream"
    else:
        data = file_or_bytes
        ct = content_type or "application/octet-stream"

    # Upload
    bucket.upload(
        path,
        data,
        file_options={"content-type": ct, "upsert": "true"},
    )

    # Public URL
    public = bucket.get_public_url(path)

    public_url = None
    if isinstance(public, str):
        public_url = public
    elif isinstance(public, dict):
        public_url = public.get("publicUrl") or public.get("public_url") or public.get("url")

    if not public_url:
        raise RuntimeError(
            "upload_product_image did not return a public url. "
            "Make sure the bucket is PUBLIC (Storage → Buckets → select bucket → Public)."
        )

    return {"path": path, "public_url": public_url}