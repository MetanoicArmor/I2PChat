from __future__ import annotations

from dataclasses import dataclass

@dataclass(slots=True)
class SAMError(Exception):
    message: str
    result: str | None = None
    raw_line: str | None = None

    def __str__(self) -> str:
        if self.result:
            return f"{self.result}: {self.message}"
        return self.message


class ProtocolError(SAMError):
    pass


class LegacySAMException(SAMError):
    pass


class InvalidId(LegacySAMException):
    pass


class CantReachPeer(LegacySAMException):
    pass


class KeyNotFound(LegacySAMException):
    pass


class DuplicatedId(LegacySAMException):
    pass


class SessionClosed(SAMError):
    pass


def map_result_to_error(
    result: str,
    message: str,
    raw_line: str | None = None,
) -> SAMError:
    result_upper = (result or "").upper()
    mapping = {
        "INVALID_ID": InvalidId,
        "CANT_REACH_PEER": CantReachPeer,
        "KEY_NOT_FOUND": KeyNotFound,
        "DUPLICATED_ID": DuplicatedId,
    }
    exc_type = mapping.get(result_upper, LegacySAMException)
    return exc_type(message=message, result=result_upper, raw_line=raw_line)
