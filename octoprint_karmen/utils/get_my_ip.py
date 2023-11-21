"""Get local IP of this device

Source: https://stackoverflow.com/questions/166506/finding-local-ip-addresses-using-pythons-stdlib
"""
import socket


def get_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0)
    try:
        # doesn't even have to be reachable
        sock.connect(('10.254.254.254', 1))
        ip = sock.getsockname()[0]
    except Exception:
        return None
    finally:
        sock.close()
    return ip
