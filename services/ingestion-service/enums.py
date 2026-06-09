from enum import Enum


class FileTypeEnum(str, Enum):
    PDF = ".pdf"


class DocumentStatusEnum(str, Enum):
    PENDING    = "pending"
    PROCESSING = "processing"
    DONE       = "done"
    FAILED     = "failed"


class UserRoleEnum(str, Enum):
    SYSTEM_ADMIN  = "system_admin"
    DOMAIN_ADMIN  = "domain_admin"
    CONTRIBUTOR   = "contributor"
    READER        = "reader"


class QueueEnum(str, Enum):
    INGESTION = "ingestion"


class TaskEnum(str, Enum):
    PROCESS_DOCUMENT = "worker.tasks.process_document"