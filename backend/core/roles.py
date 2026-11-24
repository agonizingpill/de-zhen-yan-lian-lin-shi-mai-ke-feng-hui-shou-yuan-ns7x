from enum import StrEnum

class Role(StrEnum):
    OPERATOR = 'operator'
    REVIEWER = 'reviewer'
    REVIEWER2 = 'reviewer2'
    ARBITER = 'arbiter'
    VIEWER = 'viewer'

WRITE = (Role.OPERATOR, Role.REVIEWER)
REVIEW = (Role.REVIEWER,)
# 漏报复核的两名现场听音复核员与第三人裁定席
REVIEW_SEATS = (Role.REVIEWER, Role.REVIEWER2)
ARBITRATION = (Role.ARBITER,)
AUDIT_READ = tuple(Role)
READ = tuple(Role)
