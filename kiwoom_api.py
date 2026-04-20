# kiwoom_api.py
import requests
import uuid
from config import MODE

class KiwoomREST:
    # ✅ MODE 에 따라 자동 URL 선택
    BASE_URL = "https://mockapi.kiwoom.com" if MODE == "mock" \
               else "https://api.kiwoom.com"
    WS_URL   = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket" if MODE == "mock" \
               else "wss://api.kiwoom.com:10000/api/dostk/websocket"

    def __init__(self, app_key: str, secret_key: str):
        self.app_key       = app_key
        self.secret_key    = secret_key
        self.access_token  = None
        self.token_expires = ""
        self.is_mock       = MODE == "mock"

    def _get_mac(self):
        return ':'.join(['{:02X}'.format((uuid.getnode() >> i) & 0xFF)
                         for i in range(0, 48, 8)][::-1])

    def _headers(self, api_id: str):
        return {
            "Content-Type":  "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey":        self.app_key,
            "secretkey":     self.secret_key,
            "api-id":        api_id,
        }

    # ── 인증 ─────────────────────────────────────────────

    def get_token(self):
        url  = f"{self.BASE_URL}/oauth2/token"
        body = {
            "grant_type":  "client_credentials",
            "appkey":      self.app_key,
            "secretkey":   self.secret_key,
            "mac_address": self._get_mac(),
        }
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json;charset=UTF-8"},
            json=body
        )
        data = resp.json()
        if data.get("return_code") == 0:
            self.access_token  = data["token"]
            raw = data.get("expires_dt", "")
            if len(raw) == 14:
                self.token_expires = (
                    f"{raw[:4]}-{raw[4:6]}-{raw[6:8]} "
                    f"{raw[8:10]}:{raw[10:12]}:{raw[12:14]}"
                )
            return self.access_token
        raise Exception(f"토큰 발급 실패: {data.get('return_msg', data)}")

    # ── 계좌 목록 (ka00001) ───────────────────────────────
    # 응답: {"acctNo": "1234567890 9876543210", ...}
    # acctNo 는 공백으로 구분된 계좌번호 문자열

    def get_account_list(self):
        resp = requests.post(
            f"{self.BASE_URL}/api/dostk/acnt",
            headers=self._headers("ka00001"),
            json={}
        )
        return resp.json()

    # ── 시세 조회 (ka10001) ───────────────────────────────

    def get_current_price(self, stock_code: str):
        resp = requests.post(
            f"{self.BASE_URL}/api/dostk/stkinfo",
            headers=self._headers("ka10001"),
            json={"stk_cd": stock_code}
        )
        return resp.json()

    # ── 계좌 잔고 (kt00018) ───────────────────────────────

    def get_balance(self, account_no: str):
        """계좌평가잔고내역요청 (kt00018)"""
        # ✅ 문서 기준: acnt_no 파라미터 없음, qry_tp + dmst_stex_tp 만 필요
        resp = requests.post(
            f"{self.BASE_URL}/api/dostk/acnt",
            headers=self._headers("kt00018"),
            json={
                "qry_tp": "1",  # 1:합산
                "dmst_stex_tp": "KRX",
            }
        )
        return resp.json()

    # ── 매수 주문 (kt10000) ───────────────────────────────

    def buy(self, stock_code: str, quantity: int, price: int = 0):
        """
        매수 주문
        price=0 → 시장가 (trde_tp=3)
        price>0 → 지정가 (trde_tp=0)
        """
        resp = requests.post(
            f"{self.BASE_URL}/api/dostk/ordr",
            headers=self._headers("kt10000"),
            json={
                "dmst_stex_tp": "KRX",
                "stk_cd":       stock_code,
                "ord_qty":      str(quantity),
                "ord_uv":       str(price) if price > 0 else "",
                "trde_tp":      "0" if price > 0 else "3",   # 0=지정가, 3=시장가
                "cond_uv":      "",
            }
        )
        return resp.json()

    # ── 매도 주문 (kt10001) ───────────────────────────────

    def sell(self, stock_code: str, quantity: int, price: int = 0):
        """
        매도 주문
        price=0 → 시장가 (trde_tp=3)
        price>0 → 지정가 (trde_tp=0)
        """
        resp = requests.post(
            f"{self.BASE_URL}/api/dostk/ordr",
            headers=self._headers("kt10001"),   # ✅ 매도는 kt10001
            json={
                "dmst_stex_tp": "KRX",
                "stk_cd":       stock_code,
                "ord_qty":      str(quantity),
                "ord_uv":       str(price) if price > 0 else "",
                "trde_tp":      "0" if price > 0 else "3",
                "cond_uv":      "",
            }
        )
        return resp.json()

    # ── 하위 호환 (account_no 파라미터 무시) ─────────────

    def send_order(self, account_no: str, stock_code: str,
                   order_type: str, quantity: int, price: int = 0):
        """
        수동 주문용 래퍼
        order_type: "1"=매수, "2"=매도
        """
        if order_type == "1":
            return self.buy(stock_code, quantity, price)
        else:
            return self.sell(stock_code, quantity, price)