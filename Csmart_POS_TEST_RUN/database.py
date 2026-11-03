# -*- coding: utf-8 -*-
"""موزّع قاعدة البيانات — يختار SQLite أو MySQL حسب الإعدادات"""
import os

DB_TYPE = os.environ.get("DB_TYPE", "sqlite").lower()

if DB_TYPE == "mysql":
    from database_mysql import *  # noqa: F401,F403
    from database_mysql import IntegrityError  # noqa: F401
else:
    from database_sqlite import *  # noqa: F401,F403
    from database_sqlite import IntegrityError  # noqa: F401
