import dataclasses
from typing import Optional, List, Dict, Any


@dataclasses.dataclass
class PRInfo:
    number: int
    title: str
    body: str
    url: str
    user: str
    merged_at: Optional[str]
    labels: List[str]
    base_ref: str
    head_ref: str
    files: Optional[List[Dict[str, Any]]] = None