"""
Pydantic スキーマ定義（API リクエスト/レスポンス用）
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


# ============ 授業（Class）関連 ============


class ClassBase(BaseModel):
    """授業登録の基本情報"""

    weekday: str = Field(..., description="曜日（月曜日〜日曜日）")
    period: str = Field(..., description="時限（1〜6）")
    subject: str = Field(..., description="授業名")
    room: str = Field(..., description="教室")


class ClassCreate(ClassBase):
    """授業新規作成リクエスト"""

    pass


class ClassUpdate(BaseModel):
    """授業更新リクエスト"""

    weekday: Optional[str] = None
    period: Optional[str] = None
    subject: Optional[str] = None
    room: Optional[str] = None


class ClassResponse(ClassBase):
    """授業情報レスポンス"""

    # JSONには id フィールドがないため、weekday+period を複合キーとして使用
    # またはメモリ上で一意なインデックスを返す
    day: int = Field(..., description="曜日（0-6: 月〜日）")

    class Config:
        from_attributes = True


# ============ 補講（Makeup）関連 ============


class MakeupBase(BaseModel):
    """補講の基本情報"""

    date: str = Field(..., description="日付（YYYY-MM-DD）")
    time: str = Field(..., description="時刻（HH:MM）")
    subject: str = Field(..., description="授業名")
    room: str = Field(..., description="教室")


class MakeupCreate(MakeupBase):
    """補講新規作成リクエスト"""

    pass


class MakeupUpdate(BaseModel):
    """補講更新リクエスト"""

    date: Optional[str] = None
    time: Optional[str] = None
    subject: Optional[str] = None
    room: Optional[str] = None


class MakeupResponse(MakeupBase):
    """補講情報レスポンス"""

    class Config:
        from_attributes = True


# ============ 手動休講（Cancel）関連 ============


class CancelBase(BaseModel):
    """休講の基本情報"""

    date: str = Field(..., description="日付（YYYY-MM-DD）")
    subject: str = Field(..., description="授業名")


class CancelCreate(CancelBase):
    """休講新規作成リクエスト"""

    pass


class CancelResponse(CancelBase):
    """休講情報レスポンス"""

    class Config:
        from_attributes = True


# ============ 試験時間割（Exam Schedule）関連 ============


class ExamClassBase(BaseModel):
    """試験時間割内の授業情報"""

    weekday: str = Field(..., description="曜日（月曜日〜日曜日）")
    period: str = Field(..., description="時限（1〜6）")
    subject: str = Field(..., description="授業名")
    room: str = Field(..., description="教室")
    time: Optional[str] = Field(None, description="開始時刻（HH:MM）試験期間中のみ")


class ExamScheduleBase(BaseModel):
    """試験時間割の基本情報"""

    name: str = Field(..., description="時間割名")
    start_date: str = Field(..., description="開始日（YYYY-MM-DD）")
    end_date: str = Field(..., description="終了日（YYYY-MM-DD）")


class ExamScheduleCreate(ExamScheduleBase):
    """試験時間割新規作成リクエスト"""

    classes: Optional[List[ExamClassBase]] = Field(default=[], description="授業リスト")


class ExamScheduleResponse(ExamScheduleBase):
    """試験時間割レスポンス"""

    classes: List[ExamClassBase] = Field(default=[], description="授業リスト")

    class Config:
        from_attributes = True


class ExamClassAddRequest(ExamClassBase):
    """試験時間割に授業を追加するリクエスト"""

    pass


class ExamClassRemoveRequest(BaseModel):
    """試験時間割から授業を削除するリクエスト"""

    weekday: str = Field(..., description="曜日")
    period: str = Field(..., description="時限")


# ============ 通知設定（Notify Settings）関連 ============


class NotifySettings(BaseModel):
    """通知設定"""

    type: str = Field(..., description="通知タイプ（normal または exam）")
    first: int = Field(..., description="最初の通知までの分数")
    second: int = Field(..., description="２番目の通知までの分数")


class NotifyShowResponse(BaseModel):
    """通知設定表示レスポンス"""

    normal: NotifySettings
    exam: NotifySettings


# ============ ユーザー設定（Settings）関連 ============


class PeriodTimeUpdate(BaseModel):
    """時限開始時刻設定"""

    period: str = Field(..., description="時限（1〜6）")
    time: str = Field(..., description="開始時刻（HH:MM）")


class TermSettings(BaseModel):
    """学期設定"""

    term: str = Field(..., description="学期（前期 または 後期）")
    start_date: Optional[str] = Field(None, description="開始日（YYYY-MM-DD）")
    end_date: Optional[str] = Field(None, description="終了日（YYYY-MM-DD）")
    class_count: Optional[int] = Field(None, description="授業回数")


class TermSettingsUpdate(BaseModel):
    """学期設定更新リクエスト"""

    term: str = Field(..., description="学期（前期 または 後期）")
    start_date: Optional[str] = Field(None, description="開始日（YYYY-MM-DD）")
    end_date: Optional[str] = Field(None, description="終了日（YYYY-MM-DD）")
    class_count: Optional[int] = Field(None, description="授業回数")


class SettingsShowResponse(BaseModel):
    """設定情報レスポンス"""

    period_times: dict = Field(..., description="時限ごとの開始時刻")
    term_settings: dict = Field(default={}, description="学期設定（前期・後期）")


# ============ エラーレスポンス ============


class ErrorResponse(BaseModel):
    """エラーレスポンス"""

    detail: str = Field(..., description="エラーメッセージ")
    status_code: int = Field(..., description="HTTPステータスコード")


# ============ ユーザー認証 ============


class UserInfo(BaseModel):
    """ユーザー情報"""

    user_id: int = Field(..., description="Discord ユーザーID")
    username: str = Field(..., description="ユーザー名")
    avatar: Optional[str] = Field(None, description="アバター URL")


class AuthResponse(BaseModel):
    """認証成功レスポンス"""

    access_token: str = Field(..., description="アクセストークン")
    token_type: str = Field(default="bearer", description="トークンタイプ")
    user: UserInfo = Field(..., description="ユーザー情報")


class LoginUrlResponse(BaseModel):
    """ログインURLレスポンス"""

    url: str = Field(..., description="Discord OAuth2 認証 URL")
    state: str = Field(..., description="CSRF対策用 state パラメータ")


# ============ 一般レスポンス ============


class SuccessResponse(BaseModel):
    """成功レスポンス"""

    message: str = Field(..., description="成功メッセージ")
    data: Optional[dict] = Field(None, description="返されるデータ")


class ListResponse(BaseModel):
    """リスト取得レスポンス"""

    count: int = Field(..., description="取得件数")
    items: List[dict] = Field(..., description="アイテムリスト")
