from .sentry import SentryWrapper
from .get_my_ip import get_ip

def parse_path_whitelist(path_whitelist_settings: str) -> tuple:
    return tuple(filter(None, path_whitelist_settings.split(';')))
