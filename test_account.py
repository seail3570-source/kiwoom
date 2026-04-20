# test_account.py
import requests
from config import APP_KEY, SECRET_KEY
import uuid

BASE_URL = "https://api.kiwoom.com"

def get_token():
    mac = ':'.join(['{:02X}'.format((uuid.getnode() >> i) & 0xFF)
                    for i in range(0, 48, 8)][::-1])
    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        headers={"Content-Type": "application/json;charset=UTF-8"},
        json={
            "grant_type":  "client_credentials",
            "appkey":      APP_KEY,
            "secretkey":   SECRET_KEY,
            "mac_address": mac,
        }
    )
    return resp.json()["token"]

token = get_token()

resp = requests.post(
    f"{BASE_URL}/api/dostk/acnt",
    headers={
        "Content-Type":  "application/json;charset=UTF-8",
        "authorization": f"Bearer {token}",
        "appkey":        APP_KEY,
        "secretkey":     SECRET_KEY,
        "api-id":        "ka00001",
    },
    json={}
)
data = resp.json()
print("응답 전체:", data)

# acctNo 파싱
acct_str = data.get("acctNo", "")
accounts = [a.strip() for a in acct_str.split() if a.strip()]
print(f"계좌 수: {len(accounts)}개")
for i, a in enumerate(accounts):
    print(f"  계좌 {i+1}: {a}")