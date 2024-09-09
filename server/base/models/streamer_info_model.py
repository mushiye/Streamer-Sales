#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File    :   streamer_info_model.py
@Time    :   2024/08/30
@Project :   https://github.com/PeterH0323/Streamer-Sales
@Author  :   HinGwenWong
@Version :   1.0
@Desc    :   主播信息数据结构
"""

from sqlmodel import Field, SQLModel


# =======================================================
#                      数据库模型
# =======================================================
class StreamerInfo(SQLModel, table=True):

    streamer_id: int | None = Field(default=None, primary_key=True, unique=True)
    name: str = Field(index=True, unique=True)
    character: str = ""
    avatar: str = ""  # 头像

    tts_weight_tag: str = ""  # 艾丝妲
    tts_reference_sentence: str = ""
    tts_reference_audio: str = ""

    poster_image: str = ""
    base_mp4_path: str = ""

    delete: bool = False

    user_id: int | None = Field(default=None, foreign_key="userinfo.user_id")
