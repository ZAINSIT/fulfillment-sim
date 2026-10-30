"""Pydantic models enforce the data dictionary. If a generator emits a
row that violates the dictionary, validation fails loudly here rather
than silently corrupting downstream tables."""

from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator

class Category(str, Enum):
    apparel = "apparel"; electronics = "electronics"; grocery = "grocery"
    home = "home"; beauty = "beauty"

class NodeType(str, Enum):
    warehouse = "warehouse"; darkstore = "darkstore"; store = "store"

class Zone(str, Enum):
    Z1="Z1"; Z2="Z2"; Z3="Z3"; Z4="Z4"; Z5="Z5"; Z6="Z6"; Z7="Z7"; Z8="Z8"

class ServiceLevelName(str, Enum):
    standard = "standard"; express = "express"; same_day = "same_day"

class SKU(BaseModel):
    sku_id: str = Field(pattern=r"^SKU-\d{6}$")
    name: str = Field(min_length=1, max_length=120)
    category: Category
    list_price_paise: int = Field(gt=0)
    weight_g: int = Field(ge=1, le=50_000)
    hazmat: bool
    active_from: date
    active_to: date | None = None

    @model_validator(mode="after")
    def _dates(self):
        if self.active_to and self.active_to < self.active_from:
            raise ValueError("active_to < active_from")
        return self

class FulfillmentNode(BaseModel):
    node_id: str = Field(pattern=r"^FUL-2026-\d{6}$")
    node_type: NodeType
    zone: Zone
    daily_capacity_units: int = Field(gt=0)
    cutoff_hour_local: int = Field(ge=0, le=23)
    timezone: str
    active: bool = True

class Inventory(BaseModel):
    inventory_id: str
    node_id: str
    sku_id: str
    on_hand: int = Field(ge=0)
    reserved: int = Field(ge=0)
    safety_stock: int = Field(ge=0)
    as_of_ts: datetime

    @model_validator(mode="after")
    def _inv(self):
        if self.reserved > self.on_hand:
            raise ValueError("reserved > on_hand")
        if self.safety_stock > self.on_hand:
            raise ValueError("safety_stock > on_hand")
        return self

class WarehouseCapacity(BaseModel):
    capacity_id: str
    node_id: str
    date: date
    units_capacity: int = Field(ge=0)          # 0 allowed on holidays
    units_used: int = Field(ge=0)
    is_holiday: bool = False

    @model_validator(mode="after")
    def _cap(self):
        if self.is_holiday:
            if self.units_capacity != 0:
                raise ValueError("holiday capacity must be 0")
            if self.units_used != 0:
                raise ValueError("holiday units_used must be 0")
        else:
            if self.units_capacity < 1:
                raise ValueError("non-holiday capacity must be >= 1")
            if self.units_used > self.units_capacity:
                raise ValueError("units_used > units_capacity")
        return self

class Carrier(BaseModel):
    carrier_id: str = Field(pattern=r"^CAR-\d{3}$")
    name: str
    supports_hazmat: bool
    service_zones: list[Zone]
    service_levels: list[ServiceLevelName]
    cutoff_hour_local: int = Field(ge=0, le=23)
    timezone: str

    @field_validator("service_zones", "service_levels")
    @classmethod
    def _nonempty(cls, v):
        if not v:
            raise ValueError("must be non-empty")
        return v

class ServiceLevel(BaseModel):
    service_level_id: str
    name: ServiceLevelName
    transit_days_min: int = Field(ge=0)
    transit_days_max: int = Field(ge=0)
    cutoff_required: bool

    @model_validator(mode="after")
    def _transit(self):
        if self.transit_days_min > self.transit_days_max:
            raise ValueError("transit_days_min > transit_days_max")
        return self