"""Request bodies for the HTTP bridge."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ColorBody(BaseModel):
    red: int = Field(ge=0, le=255)
    green: int = Field(ge=0, le=255)
    blue: int = Field(ge=0, le=255)
    brightness: int | None = Field(default=None, ge=0, le=100)


class WhiteBody(BaseModel):
    temperature: int | None = Field(default=None, ge=0, le=100,
                                    description="0 = warm, 100 = cold")
    brightness: int | None = Field(default=None, ge=0, le=100)


class BrightnessBody(BaseModel):
    percent: int = Field(ge=0, le=100)


class DawnBody(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    fade_minutes: int = Field(default=30, ge=0, le=63,
                              description="length of the fade-up ramp")
    brightness: int = Field(default=100, ge=1, le=100)
    temperature: int = Field(default=0, ge=0, le=100, description="0 = warm")
    weekdays: int = Field(default=0x7F, ge=0, le=0x7F,
                          description="7-bit mask, 0x7F = every day")
    enabled: bool = True


class SequenceBody(BaseModel):
    sequence: int = Field(ge=0, le=6, description="built-in colour sequence 0..6")
    duration_ms: int = Field(default=3072, ge=100, le=60000,
                             description="time per colour")