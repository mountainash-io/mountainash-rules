from pydantic import BaseModel


class Aggregate(BaseModel):
    column_name: str
    operation: str = "sum"
