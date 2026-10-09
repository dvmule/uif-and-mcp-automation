from pydantic import BaseModel


class OverviewStats(BaseModel):
    total_schemas: int
    schemas_with_compatibility_rules: int
    non_compliant_schemas: int
