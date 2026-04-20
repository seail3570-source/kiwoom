# test_balance.py
import requests
import uuid
from config import REAL_APP_KEY, REAL_SECRET_KEY

BASE_URL = "https://api.kiwoom.com"

def get_token():
    mac = ':'.join(['{:02X}'.format((uuid.getnode() >> i) & 0xFF)
                    for i in range(0, 48, 8)][::-1])
    resp = requests.post(
        f"{BASE_URL}/oauth2/token",
        headers={"Content-Type": "application/json;charset=UTF-8"},
        json={
            "grant_type":  "client_credentials",
            "appkey":      REAL_APP_KEY,
            "secretkey":   REAL_SECRET_KEY,
            "mac_address": mac,
        }
    )
    return resp.json()["token"]

token = get_token()
print("✅ 토큰 발급 성공\n")

# ── 계좌번호 2개 모두 테스트
for acnt in ["6610889910", "두번째_계좌번호"]:
    resp = requests.post(
        f"{BASE_URL}/api/dostk/acnt",
        headers={
            "Content-Type":  "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "appkey":        REAL_APP_KEY,
            "secretkey":     REAL_SECRET_KEY,
            "api-id":        "kt00018",
        },
        json={
            "acnt_no":      acnt,
            "qry_tp":       "1",
            "dmst_stex_tp": "KRX",
        }
    )
    data = resp.json()
    print(f"=== 계좌: {acnt} ===")
    print(f"  총평가금액  : {data.get('tot_evlt_amt', '')}")
    print(f"  총평가손익  : {data.get('tot_evlt_pl', '')}")
    print(f"  수익률      : {data.get('tot_prft_rt', '')}")
    print(f"  추정예탁자산: {data.get('prsm_dpst_aset_amt', '')}")
    print(f"  return_code : {data.get('return_code', '')}")
    print(f"  return_msg  : {data.get('return_msg', '')}")
    print()