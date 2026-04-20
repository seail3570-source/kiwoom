# config.py
import os
from dotenv import load_dotenv

load_dotenv()

MODE = os.getenv("KIWOOM_MODE", "real")

# 실투자 키
REAL_APP_KEY    = os.getenv("REAL_APP_KEY",    "")
REAL_SECRET_KEY = os.getenv("REAL_SECRET_KEY", "")

# 모의투자 키
MOCK_APP_KEY    = os.getenv("MOCK_APP_KEY",    "")
MOCK_SECRET_KEY = os.getenv("MOCK_SECRET_KEY", "")

# 모드에 따라 기본 키 선택
if MODE == "mock":
    APP_KEY    = MOCK_APP_KEY
    SECRET_KEY = MOCK_SECRET_KEY
else:
    APP_KEY    = REAL_APP_KEY
    SECRET_KEY = REAL_SECRET_KEY

# 계좌 목록
MANUAL_ACCOUNTS = [
    acnt for acnt in [
        os.getenv("KIWOOM_ACCOUNT_1", ""),
        os.getenv("KIWOOM_ACCOUNT_2", ""),
    ] if acnt
]