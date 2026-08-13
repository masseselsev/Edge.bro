"""Global orchestrator settings and the sub-objects they nest."""
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


class RetentionPolicySchema(BaseModel):
    type: str = Field(default='interval')  # 'interval', 'count', 'timeframe'
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    keep_last: int = Field(default=5, ge=1)
    within_value: int = Field(default=3, ge=1)
    within_unit: str = Field(default='m')  # 'd', 'w', 'm', 'y'

class ExclusionSchema(BaseModel):
    pattern: str
    comment: str

class CredentialSchema(BaseModel):
    id: str
    username: str
    password: str
    comment: str = ""

class SettingsBase(BaseModel):
    borg_ssh_port: int = Field(default=12345, ge=1, le=65535)
    borg_repo_path: str = Field(default='/data/borg')
    keep_daily: int = Field(default=7, ge=0)
    keep_weekly: int = Field(default=4, ge=0)
    keep_monthly: int = Field(default=6, ge=0)
    global_exclusions: List[ExclusionSchema] = Field(default=[])
    orchestrator_ip: str = Field(default='')
    orchestrator_behind_nat: bool = Field(default=False)
    timezone: str = Field(default='Browser Local')
    language: str = Field(default='en')
    retention_policy: Optional[RetentionPolicySchema] = None
    default_compression: str = Field(default='zstd:3')
    default_cpu_quota: Optional[int] = Field(default=30, ge=0, le=400)
    server_ips: Optional[List[str]] = Field(default=[])
    max_kiosk_isos: int = Field(default=5, ge=1)
    server_name: str = Field(default='edge-bro')
    bootstrap_credentials: List[CredentialSchema] = Field(default=[])
    default_credentials_id: Optional[str] = Field(default='')
    server_net_capacity_mbps: int = Field(default=1000, ge=1)


    @field_validator('server_name')
    @classmethod
    def validate_server_name(cls, v: str) -> str:
        import re
        # Used as an ISO filename prefix, so it is normalised to lowercase and
        # kept free of dots and separators.
        v = v.lower()
        if not re.match(r'^[a-z0-9][a-z0-9_-]*$', v):
            raise ValueError(
                "Server name must start with a letter or digit and contain only "
                "letters, numbers, hyphens, and underscores — no dots or spaces."
            )
        return v

class SettingsResponse(SettingsBase):
    id: int
    available_ips: Optional[List[str]] = None
    borg_host_data_path: Optional[str] = None

    class Config:
        from_attributes = True
