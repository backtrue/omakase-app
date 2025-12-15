from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class UserPreferences(BaseModel):
    language: str = Field(default="zh-TW")


class ScanRequest(BaseModel):
    image_base64: str
    user_preferences: UserPreferences = Field(default_factory=UserPreferences)


ImageStatus = Literal["pending", "ready", "none", "failed"]


class MenuItem(BaseModel):
    id: str
    original_name: str
    translated_name: str
    description: str
    tags: List[str]
    is_top3: bool
    image_status: ImageStatus
    image_prompt: str
    romanji: str = ""


class MenuDataEvent(BaseModel):
    session_id: str
    items: List[MenuItem]


class VlmMenuItem(BaseModel):
    dish_key: Optional[str] = Field(default="")
    original_name: str
    translated_name: str
    description: Optional[str] = Field(default="")
    tags: Optional[List[str]] = Field(default_factory=list)
    is_top3: Optional[bool] = Field(default=False)
    image_prompt: Optional[str] = Field(default="")
    romanji: Optional[str] = Field(default="")


class VlmMenuResponse(BaseModel):
    menu_items: List[VlmMenuItem]


class VlmDishStringsResponse(BaseModel):
    dish_strings: List[str]
