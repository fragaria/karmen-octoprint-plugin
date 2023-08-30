"""
Binary packager compatible with javaScript websocket_proxy buffer-struct
module.
"""

from functools import cached_property
from collections import namedtuple
from abc import (
    abstractmethod,
    ABC,
)
import inspect
import struct
import io
import logging

logger = logging.getLogger(__name__)


UnpackResult = namedtuple("UnpackResult", "offset_change value")


class Field(ABC):
    "base Field class"

    # conter of fields declared so far. used to index fields to maintain it's
    # order
    __counter = 0

    def __init__(self, size=0):
        self.declaration_order = Field.__counter
        self.size = size
        Field.__counter += 1
        self.name = None  # will be set during use of the field

    @property
    @abstractmethod
    def _packed_size(self):
        "return field size after is is being packed"
        return self.size

    def pack(self, value):
        "pack value to bytes"
        return self._pack_value(value)

    def unpack(self, source, offset=0):
        "extracts value from bytes starting from offset"
        return UnpackResult(self._packed_size, self._unpack_value(source, offset))

    @abstractmethod
    def _pack_value(self, value):
        "pack field value to binary format"

    @abstractmethod
    def _unpack_value(self, source: bytes, offset: int):
        "unpack binary data from `source` at `offset` and return unpacked value"

    def __str__(self):
        return f"Field {self.name}"

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name})"


class StructField(Field):
    "base field for values packed by struct library"

    def __init__(self, size=None):
        super().__init__(size=size)
        self.struct_format = None

    def _pack_value(self, value):
        try:
            return struct.pack(self.struct_format, value)
        except struct.error as error:
            raise SerializingError(*error.args) from error

    def _unpack_value(self, source, offset):
        return struct.unpack_from(self.struct_format, source, offset)[0]

    @cached_property
    def _packed_size(self):
        return struct.calcsize(self.struct_format)


class IntField(StructField):
    "represent integer values"
    SIZE_TO_FORMAT = {
        True: {
            1: "<b",
            2: "<h",
            4: "<i",
            8: "<q",
        },
        False: {
            1: "<B",
            2: "<H",
            4: "<I",
            8: "<Q",
        },
    }

    def __init__(self, size=4, signed=True):
        super().__init__(size=size)
        self.signed = signed
        try:
            self.struct_format = self.SIZE_TO_FORMAT[self.signed][size]
        except KeyError as error:
            raise InvalidFormatError(
                f'Allowed Int sizes are {", ".join(map(str, IntField.SIZE_TO_FORMAT[self.signed].keys()))} '
                f'(not "{size}").'
            ) from error


class UIntField(IntField):
    "represent unsigned integer values"

    def __init__(self, size=4):
        super().__init__(size=size, signed=False)


class FloatField(StructField):
    "represent integer values"
    SIZE_TO_FORMAT = {
        4: "<f",
        8: "<d",
    }

    def __init__(self, size=4, signed=True):
        super().__init__(size=size)
        self.signed = signed
        try:
            self.struct_format = self.SIZE_TO_FORMAT[size]
        except KeyError as error:
            raise InvalidFormatError(
                f'Allowed Float sizes are {", ".join(FloatField.SIZE_TO_FORMAT.keys())} '
                f'(not "{size}").'
            ) from error


class BytesField(Field):
    """represent byte field
    This field has variable length. The binary representation therefore starts with real record length.
    Size parameter of this field limits maximal size of the field.
    The internal binary representation is as follows (<name: lenght in bytes>):
    `<size: 4> <value: $size>`
    where
       <size> is little endian unsigned long (4 bytes)
       <bytes> is <size> bytes storing the value
    """

    struct_size_format = "<L"
    struct_size_length = struct.calcsize(struct_size_format)

    def __init__(self, size=0):
        super().__init__(size=size)

    def pack(self, value):
        if not isinstance(value, bytes):
            raise ValueError(
                f"BytesField can pack only bytes not {value.__class__.__name__}"
            )
        return self.pack_size(value) + self._pack_value(value)

    def _pack_value(self, value):
        if self.size and len(value) > self.size - struct.calcsize("<L"):
            raise SerializingError("Value does not fit in the defined size.")
        return value

    def pack_size(self, value):
        return struct.pack(self.struct_size_format, len(value))

    @staticmethod
    def unpack_size(source, offset):
        return struct.unpack_from("<L", source, offset)[0]

    def unpack(self, source, offset=0) -> UnpackResult:
        size = self.unpack_size(source, offset)
        offset += self.struct_size_length
        return UnpackResult(
            self.struct_size_length + size, source[offset : offset + size]
        )

    def _unpack_value(self, source, offset=0):
        raise NotImplementedError(
            "Unpack value is unused in BytesField and should be never called."
        )

    @property
    def _packed_size(self):
        raise NotImplementedError("Packed size is dynamic for BytesField.")


class Struct(type):
    "represents model definition"

    @classmethod
    def get_fields(cls) -> "list[Field]":
        "get fields declared on this class"
        fields = getattr(cls, "_fields", None)
        if fields is None:
            fields = dict(
                sorted(
                    inspect.getmembers(cls, lambda o: isinstance(o, Field)),
                    key=lambda i: i[1].declaration_order,
                )
            )

            for field_name, field in fields.items():
                field.name = field_name
            cls._fields = fields
        return cls._fields

    @classmethod
    def pack(
        cls, data: dict, destination: io.BufferedIOBase = None
    ) -> io.BufferedIOBase:
        "pack dictionary based on definitions from fields"
        if destination is None:
            destination = io.BytesIO()
        for field in cls.get_fields().values():
            packed_data = field.pack(data[field.name])
            logger.debug(
                f"Packed field {field.name}: {data[field.name]!r} to {packed_data.hex()!r} ({len(packed_data)} bytes)"
            )
            destination.write(packed_data)
        return destination

    @classmethod
    def unpack(cls, source: bytes) -> dict:
        "unpack dictionary data from bytes based on definitions from fields"
        data = {}
        offset = 0
        for field in cls.get_fields().values():
            packed_size, data[field.name] = field.unpack(source, offset)
            offset += packed_size
        return data


class InvalidFormatError(RuntimeError):
    "general exception for an improperly configured field"


class SerializingError(RuntimeError):
    "error serializing value"
