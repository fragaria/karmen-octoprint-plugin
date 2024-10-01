import time


def parse_path_whitelist(path_whitelist_settings: str) -> tuple:
    return tuple(filter(None, path_whitelist_settings.split(';')))

def wait_till_true(condition: callable, max_timeout=0.5):
    begin = time.monotonic()
    result = False
    while not result and time.monotonic() - begin <= max_timeout:
        result = condition()
        if result:
            break
        time.sleep(0.1)
    return result
