from sqlalchemy import (JSON, CheckConstraint, Column, DateTime, ForeignKey,
                        Integer, String, Table, UniqueConstraint)

from core.db import metadata

# 漏报（啸叫）复核批次：选定一批已签诊断的声检窗
review_batches = Table(
    "review_batches", metadata,
    Column("code", String(24), primary_key=True),
    Column("created_by", String(80), nullable=False),
    Column("created_at", DateTime, nullable=False),
)

# 批次内的复核窗，冻结当时的机器判定（1=机器判为啸叫，0=未告警）
review_windows = Table(
    "review_batch_windows", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch", String(24), ForeignKey("review_batches.code"), nullable=False),
    Column("finding", String(24), ForeignKey("feedback_findings.code"), nullable=False),
    Column("window", String(24), ForeignKey("sound_windows.code"), nullable=False),
    Column("machine_howl", Integer, nullable=False),
    UniqueConstraint("batch", "window"),
    UniqueConstraint("batch", "finding"),
    CheckConstraint("machine_howl IN (0,1)"),
)

# 两名现场听音复核员各交一份；交齐前互不可见
review_listens = Table(
    "review_listens", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch", String(24), ForeignKey("review_batches.code"), nullable=False),
    Column("window", String(24), ForeignKey("sound_windows.code"), nullable=False),
    Column("seat", String(2), nullable=False),
    Column("verdict", String(8), nullable=False),
    Column("note", String(240)),
    Column("reviewer", String(80), nullable=False),
    Column("updated_at", DateTime, nullable=False),
    UniqueConstraint("batch", "window", "seat"),
    CheckConstraint("seat IN ('a','b')"),
    CheckConstraint("verdict IN ('howl','clear')"),
)

# 意见相反的窗交第三人裁定
review_arbitrations = Table(
    "review_arbitrations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch", String(24), ForeignKey("review_batches.code"), nullable=False),
    Column("window", String(24), ForeignKey("sound_windows.code"), nullable=False),
    Column("verdict", String(8), nullable=False),
    Column("note", String(240)),
    Column("arbiter", String(80), nullable=False),
    Column("decided_at", DateTime, nullable=False),
    UniqueConstraint("batch", "window"),
    CheckConstraint("verdict IN ('howl','clear')"),
)

# 封存版本：同一时点的漏报率与逐窗依据快照；重试不新增版本
review_versions = Table(
    "review_versions", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("batch", String(24), ForeignKey("review_batches.code"), nullable=False),
    Column("version", Integer, nullable=False),
    Column("fingerprint", String(64), nullable=False),
    Column("metrics", JSON, nullable=False),
    Column("windows", JSON, nullable=False),
    Column("sealed_by", String(80), nullable=False),
    Column("sealed_at", DateTime, nullable=False),
    UniqueConstraint("batch", "version"),
    UniqueConstraint("batch", "fingerprint"),
)
