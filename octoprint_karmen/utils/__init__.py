from .sentry import SentryWrapper

def parse_path_whitelist(path_whitelist_settings: str) -> tuple:
    return tuple(filter(None, path_whitelist_settings.split(';')))
