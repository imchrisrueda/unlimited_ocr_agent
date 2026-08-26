from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

MappingStatus = Literal["pending", "mapped", "unaligned"]


@dataclass
class PageArtifact:
    page_number: int  # 1-based
    image_path: Path
    raw_ocr: Optional[str] = None
    ocr_mapping_status: MappingStatus = "pending"
    mapping_error: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.image_path, Path):
            self.image_path = Path(self.image_path)
