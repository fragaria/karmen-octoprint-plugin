# implementation proposed by @theheadofabroom at Https://Stackoverflow.Com/Questions/6760685/Creating-A-Singleton-In-Python
from threading import Lock

SINGLETON_LOCK = Lock()

class Singleton(type):
    """Make class a singleton

    Usage:
    class MyClass(metaclass=Singleton)
    """
    _instances = {}
    def __call__(cls, *args, **kwargs):
        with SINGLETON_LOCK:
            if cls not in cls._instances:
                cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
            return cls._instances[cls]
