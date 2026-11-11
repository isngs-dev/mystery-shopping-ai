from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"          # Level 1
    ADMIN = "admin"                      # Level 2
    MANAGER = "manager"                  # Level 3 - Editor & Manager
    EDITOR = "editor"                    # Level 4
    QC_REVIEWER = "qc_reviewer"          # QC Lead / Reviewer
    AI_LEAD = "ai_lead"                  # AI Executive / AI Lead
    PROCESS_OWNER = "process_owner"      # Operations Process Owner
    OPS_SPONSOR = "ops_sponsor"          # Operations Sponsor
    CLIENT_POC = "client_poc"            # Project / Client POC
    REPORTING = "reporting"              # Management / Reporting User


ROLE_LEVEL = {
    Role.SUPER_ADMIN: 1,
    Role.ADMIN: 2,
    Role.OPS_SPONSOR: 2,
    Role.PROCESS_OWNER: 2,
    Role.AI_LEAD: 2,
    Role.REPORTING: 2,
    Role.MANAGER: 3,
    Role.QC_REVIEWER: 3,
    Role.EDITOR: 4,
    Role.CLIENT_POC: 4,
}

ROLE_LABEL = {
    Role.SUPER_ADMIN: "Super Admin",
    Role.ADMIN: "Admin",
    Role.MANAGER: "Editor & QC Manager",
    Role.EDITOR: "Editor",
    Role.QC_REVIEWER: "Editor & QC Lead",
    Role.AI_LEAD: "AI Executive / AI Lead",
    Role.PROCESS_OWNER: "Operations Process Owner",
    Role.OPS_SPONSOR: "Operations Sponsor",
    Role.CLIENT_POC: "Project / Client POC",
    Role.REPORTING: "Management / Reporting User",
}


class FormStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    QC_REVIEW = "qc_review"
    RE_EDIT_REQUIRED = "re_edit_required"
    APPROVED = "approved"
    CLOSED = "closed"
    MANUAL_REVIEW = "manual_review_required"
    BLOCKED = "blocked"


class DocKind(StrEnum):
    RAW = "raw"
    EDITED = "edited"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


class AIStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    INFO = "info"


SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}


class FindingType(StrEnum):
    MISSED_CORRECTION = "missed_correction"
    NEW_ERROR = "new_error"
    GRAMMAR = "grammar"
    SPELLING = "spelling"
    MISSING_INFO = "missing_info"
    MEANING_CHANGE = "meaning_change"
    FORMATTING = "formatting"
    INSTRUCTION_VIOLATION = "instruction_violation"
    EXTRACTION_ISSUE = "extraction_issue"
    SECURITY = "security"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EDITED = "edited"
    NOT_APPLICABLE = "not_applicable"
    ESCALATED = "escalated"


class Decision(StrEnum):
    APPROVED = "approved"
    RE_EDIT = "re_edit_required"
    MANUAL_REVIEW = "manual_review_required"
    BLOCKED = "blocked"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    PUBLISHED = "published"
    REJECTED = "rejected"
    RETIRED = "retired"
