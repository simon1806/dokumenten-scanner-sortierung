from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DetectedDocument:
    document_type: str
    number: str
    supplier: str | None = None

    @property
    def key(self) -> tuple[str, str, str | None]:
        return self.document_type, self.number, self.supplier

    @property
    def filename(self) -> str:
        if self.supplier:
            return f"{self.document_type}-{self.supplier}-{self.number}.pdf"
        return f"{self.document_type}_{self.number}.pdf"


@dataclass(slots=True)
class DocumentGroup:
    detected: DetectedDocument
    page_indexes: list[int]


@dataclass(frozen=True, slots=True)
class ProcessResult:
    source_name: str
    success: bool
    message: str
    created_files: tuple[str, ...] = ()
