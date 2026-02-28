from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class UserOut(BaseSchema):
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    created_at: datetime
    last_login_at: Optional[datetime] = None


class ListCreate(BaseSchema):
    name: str = Field(min_length=1, max_length=200)
    template_id: Optional[str] = None


class ListUpdate(BaseSchema):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)


class ListOut(BaseSchema):
    id: str
    user_id: str
    name: str
    template_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ItemCreate(BaseSchema):
    name: str = Field(min_length=1, max_length=200)
    qty: Optional[float] = None
    sort_order: int = 0


class ItemUpdate(BaseSchema):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    qty: Optional[float] = None
    sort_order: Optional[int] = None
    purchased: Optional[bool] = None


class ItemOut(BaseSchema):
    id: str
    user_id: str
    list_id: str
    name: str
    qty: Optional[float] = None
    purchased: bool = False
    purchased_at: Optional[datetime] = None
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime


class ReorderListItems(BaseSchema):
    item_ids: list[str]


class TemplateItemCreate(BaseSchema):
    name: str = Field(min_length=1, max_length=200)
    qty: Optional[float] = None
    sort_order: int = 0


class TemplateItemUpdate(BaseSchema):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    qty: Optional[float] = None
    sort_order: Optional[int] = None


class TemplateItemOut(BaseSchema):
    id: str
    user_id: str
    template_id: str
    name: str
    qty: Optional[float] = None
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime


class TemplateCreate(BaseSchema):
    name: str = Field(min_length=1, max_length=200)
    items: list[TemplateItemCreate] = Field(default_factory=list)


class TemplateUpdate(BaseSchema):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)


class TemplateOut(BaseSchema):
    id: str
    user_id: str
    name: str
    created_at: datetime
    updated_at: datetime


class TemplateDetailOut(TemplateOut):
    items: list[TemplateItemOut] = Field(default_factory=list)


class CreateListFromTemplate(BaseSchema):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
