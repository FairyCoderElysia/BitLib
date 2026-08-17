# -*- coding: utf-8 -*-
"""SQLAlchemy ORM 模型：按 spec.md §5.1 全量建表。

本轮（M1+M2）用到的字段已实现，其余字段（content_text/summary/error_message 等）
已建齐，供后续 sprint（M3 解析入库 / M4 检索问答 / M5 爬虫推送）使用。
ChunkChild 存于 Chroma（非 SQLite），故不在此建表。
"""
from datetime import datetime

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .db import Base

# 文档状态（spec §2.4 / §5.1）
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_OFFLINE = "offline"
STATUS_FAILED = "failed"
ALL_DOC_STATUS = (STATUS_PENDING, STATUS_PROCESSING, STATUS_APPROVED,
                  STATUS_REJECTED, STATUS_OFFLINE, STATUS_FAILED)

# 角色（spec §2.1）
ROLE_ADMIN = "admin"
ROLE_DEPT_ADMIN = "dept_admin"
ROLE_USER = "user"
ALL_ROLES = (ROLE_ADMIN, ROLE_DEPT_ADMIN, ROLE_USER)

# 文档来源（spec §5.1）
SOURCE_UPLOAD = "upload"
SOURCE_CRAWL = "crawl"


class Department(Base):
    """部门。"""
    __tablename__ = "department"

    id = Column(Integer, primary_key=True)
    name = Column(String(64), unique=True, nullable=False)

    users = relationship("User", back_populates="department")


class User(Base):
    """用户：账号 / 角色 / 所属部门。"""
    __tablename__ = "user"

    id = Column(Integer, primary_key=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(128), nullable=False)  # bcrypt 哈希
    role = Column(String(16), nullable=False, default=ROLE_USER)  # admin/dept_admin/user
    department_id = Column(Integer, ForeignKey("department.id"), nullable=True)  # 空=无部门
    must_change_password = Column(Boolean, default=False)  # 首登强制改密（F1 修复）
    created_at = Column(DateTime, default=datetime.utcnow)

    department = relationship("Department", back_populates="users")


class Document(Base):
    """文档：上传/爬虫统一记录。department_id 空 = 公开。"""
    __tablename__ = "document"

    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)
    file_name = Column(String(255), nullable=False)
    file_path = Column(String(512), nullable=False)   # 私有目录内文件名（相对 upload_dir）
    file_type = Column(String(16), nullable=False)    # txt/docx/pdf/md
    file_size = Column(Integer, default=0)            # 字节
    file_hash = Column(String(64), nullable=False, index=True)  # sha256
    source_url = Column(String(512), nullable=True, index=True)  # 爬虫入库页面 URL（F22，上传恒为 NULL）
    content_text = Column(Text, default="")           # 清洗后全文（M3 填充）
    summary = Column(Text, nullable=True)             # 智能摘要（P2 占位）
    status = Column(String(16), nullable=False, default=STATUS_PENDING, index=True)
    error_message = Column(Text, nullable=True)       # processing/failed 错误信息
    is_featured = Column(Boolean, default=False)      # 重点标记
    department_id = Column(Integer, ForeignKey("department.id"), nullable=True, index=True)
    source = Column(String(16), nullable=False, default=SOURCE_UPLOAD)  # upload/crawl
    uploaded_by = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)
    approver_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    reject_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    department = relationship("Department")
    # S7：文档多部门可见。departments 为只读视图（写路径统一走
    # document_departments.set_doc_departments 维护连接表）；
    # 授权判定一律读连接表集合，department_id 仅为主部门兼容列/快照。
    departments = relationship(
        "Department",
        secondary="document_department",
        viewonly=True,
        order_by="Department.id",
    )
    uploader = relationship("User", foreign_keys=[uploaded_by])
    approver = relationship("User", foreign_keys=[approver_id])
    parents = relationship("ChunkParent", back_populates="document")


class DocumentDepartment(Base):
    """文档-部门多对多连接表（S7：文档可属于 0..N 个部门）。

    - 非空集合 = 可见部门集合；空集合 = 公开（全员可见）。
    - Document.department_id 恒等于集合中 id 最小的部门（空集合则为 NULL），
      由 document_departments.set_doc_departments 统一同步；仅作兼容/快照，
      不作为授权依据。
    """
    __tablename__ = "document_department"
    __table_args__ = (
        UniqueConstraint("document_id", "department_id",
                         name="uq_document_department_doc_dept"),
    )

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("document.id"), nullable=False, index=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=False, index=True)


class CrawlTaskDepartment(Base):
    """爬虫任务-目标部门多对多连接表（S7 F2）。

    - 非空集合 = 目标部门集合；空集合 = 公开（入库文档全员可见）。
    - CrawlTask.target_department_id 恒等于集合中 id 最小部门（空集合为 NULL），
      由 crawl_task_departments.set_crawl_task_departments 统一同步；仅作兼容/
      审计快照，不作为继承与授权依据。
    """
    __tablename__ = "crawl_task_department"
    __table_args__ = (
        UniqueConstraint("task_id", "department_id",
                         name="uq_crawl_task_department_task_dept"),
    )

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("crawl_task.id"), nullable=False, index=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=False, index=True)


class PushNotificationDepartment(Base):
    """推送-目标部门多对多连接表（S7 F3）。

    - 非空集合 = 目标部门集合；空集合 = 全员。
    - PushNotification.department_id 恒等于集合中 id 最小部门（空集合为 NULL），
      由 push_notification_departments.set_push_departments 统一同步；仅作兼容
      展示，不作为可见性判定依据。
    """
    __tablename__ = "push_notification_department"
    __table_args__ = (
        UniqueConstraint("notification_id", "department_id",
                         name="uq_push_notification_department_notif_dept"),
    )

    id = Column(Integer, primary_key=True)
    notification_id = Column(Integer, ForeignKey("push_notification.id"), nullable=False, index=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=False, index=True)


class ChunkParent(Base):
    """父块 / 上下文单元（SQLite，~1200 token），M3 使用。"""
    __tablename__ = "chunk_parent"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunk_parent_doc_idx"),
    )

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("document.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    title = Column(String(255), default="")
    text = Column(Text, nullable=False)

    document = relationship("Document", back_populates="parents")


class FavoriteFolder(Base):
    """收藏夹。"""
    __tablename__ = "favorite_folder"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Favorite(Base):
    """收藏条目：(user_id, document_id) 唯一。"""
    __tablename__ = "favorite"
    __table_args__ = (
        UniqueConstraint("user_id", "document_id", name="uq_favorite_user_doc"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    folder_id = Column(Integer, ForeignKey("favorite_folder.id"), nullable=True)  # 空=未分类收藏
    document_id = Column(Integer, ForeignKey("document.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CrawlTask(Base):
    """爬虫任务（M5）。target_department_id 空 = 公开。"""
    __tablename__ = "crawl_task"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    start_urls = Column(JSON, default=list)
    allowed_domains = Column(JSON, default=list)
    selector = Column(Text, nullable=True)   # 正文选择器，可空=智能提取
    max_depth = Column(Integer, default=1)
    target_department_id = Column(Integer, ForeignKey("department.id"), nullable=True)
    schedule = Column(String(64), default="")  # cron
    enabled = Column(Boolean, default=False)
    status = Column(String(16), default="idle")  # idle/running/disabled...
    last_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # S7 F2：只读目标部门集合视图（写路径统一走
    # crawl_task_departments.set_crawl_task_departments 维护连接表）。
    target_departments = relationship(
        "Department",
        secondary="crawl_task_department",
        viewonly=True,
        order_by="Department.id",
    )


class CrawlRunLog(Base):
    """爬虫运行记录（M5）。"""
    __tablename__ = "crawl_run_log"

    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey("crawl_task.id"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    fetched_count = Column(Integer, default=0)
    ingested_count = Column(Integer, default=0)
    updated_count = Column(Integer, default=0)        # F22：同 URL 内容变化走更新
    skipped_count = Column(Integer, default=0)
    status = Column(String(16), default="running")  # running/success/failed
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class PushNotification(Base):
    """部门推送通知（M5）。department_id 空 = 全员。"""
    __tablename__ = "push_notification"

    id = Column(Integer, primary_key=True)
    title = Column(String(128), nullable=False)
    content = Column(Text, nullable=False)
    document_id = Column(Integer, ForeignKey("document.id"), nullable=True)
    department_id = Column(Integer, ForeignKey("department.id"), nullable=True)
    created_by = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # S7 F3：只读目标部门集合视图（写路径统一走
    # push_notification_departments.set_push_departments 维护连接表）。
    departments = relationship(
        "Department",
        secondary="push_notification_department",
        viewonly=True,
        order_by="Department.id",
    )


class PushRead(Base):
    """推送已读记录。"""
    __tablename__ = "push_read"
    __table_args__ = (
        UniqueConstraint("notification_id", "user_id", name="uq_push_read"),
    )

    id = Column(Integer, primary_key=True)
    notification_id = Column(Integer, ForeignKey("push_notification.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    read_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    """审计日志：只增不删。"""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True)  # 操作人，可空
    action = Column(String(64), nullable=False, index=True)
    target_type = Column(String(32), nullable=True, index=True)
    target_id = Column(Integer, nullable=True)
    detail = Column(JSON, nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    user = relationship("User", foreign_keys=[user_id])


class SearchLog(Base):
    """搜索日志（M4 使用，供热词榜）。"""
    __tablename__ = "search_log"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    query = Column(String(255), nullable=False)
    hit_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class QASession(Base):
    """问答会话（M4）。"""
    __tablename__ = "qa_session"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    title = Column(String(255), default="")  # 首问摘要
    created_at = Column(DateTime, default=datetime.utcnow)


class QAMessage(Base):
    """问答消息。citations 为 JSON：document_id/title/snippet/parent_id。"""
    __tablename__ = "qa_message"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("qa_session.id"), nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user/assistant
    content = Column(Text, nullable=False)
    citations = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# DocumentFTS 为 FTS5 虚拟表（external content），建表 DDL 见 db._create_fts_table()，
# 同步逻辑（触发器/应用层）M3 实现。此处不声明 ORM 模型。
