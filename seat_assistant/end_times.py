"""Helpers for using the seat site's native end-time response."""

from dataclasses import dataclass

from .submission import requested_times_available, time_values


@dataclass(frozen=True)
class NativeEndTimes:
    url: str
    options: tuple[str, ...]
    code: str | int | None
    message: str

    @property
    def ok(self) -> bool:
        return self.code in (0, "0")

    def supports(self, value: str) -> bool:
        return self.ok and requested_times_available(list(self.options), [value])


def parse_native_end_times(url: str, response: dict) -> NativeEndTimes:
    body = response if isinstance(response, dict) else {}
    return NativeEndTimes(
        url=url,
        options=tuple(time_values(body, "endTimes")),
        code=body.get("code"),
        message=str(body.get("message") or ""),
    )
