from urllib.parse import urlsplit, urlunsplit


def sanitize_url(value: str) -> str:
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))
