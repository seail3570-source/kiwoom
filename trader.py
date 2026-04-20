# trader.py
import time
import json
import asyncio
import threading
import traceback
import websockets
from datetime import datetime
from PyQt5.QtCore import QObject, pyqtSignal
from kiwoom_api import KiwoomREST
from db import insert_trade


class TradePosition:
    """종목별 포지션 관리"""
    def __init__(self, stock_code, stock_name, base_price):
        self.stock_code  = stock_code
        self.stock_name  = stock_name
        self.base_price  = base_price
        self.avg_price   = 0
        self.quantity    = 0
        self.total_cost  = 0
        self.buy_levels  = set()
        self.sell_levels = set()

    def update_avg(self, buy_price, qty=1):
        self.total_cost += buy_price * qty
        self.quantity   += qty
        self.avg_price   = round(self.total_cost / self.quantity)

    def reduce_qty(self, qty=1):
        self.quantity   = max(0, self.quantity - qty)
        self.total_cost = self.avg_price * self.quantity


class AutoTrader(QObject):
    log_signal    = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    price_signal  = pyqtSignal(str, int)

    def __init__(self, api: KiwoomREST, account_no: str, settings: dict):
        super().__init__()
        self.api        = api
        self.account_no = account_no
        self.settings   = settings
        self.running    = False
        self.positions  = {}
        self._thread    = None

        # ✅ api 모드에 따라 WS_URL 자동 선택
        self.WS_URL = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket" \
                      if api.is_mock else \
                      "wss://api.kiwoom.com:10000/api/dostk/websocket"

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_signal.emit(f"[{ts}] {msg}")

    # ── WebSocket 공통 ────────────────────────────────────
    async def _ws_request(self, send_data: dict, timeout=30):
        headers = {
            "authorization": f"Bearer {self.api.access_token}",
            "appkey":        self.api.app_key,
            "secretkey":     self.api.secret_key,
        }
        async with websockets.connect(
            self.WS_URL, additional_headers=headers
        ) as ws:
            # 1. 로그인
            await ws.send(json.dumps({
                "trnm":  "LOGIN",
                "token": self.api.access_token,
            }))
            login_data = json.loads(
                await asyncio.wait_for(ws.recv(), timeout=timeout)
            )
            if login_data.get("return_code") != 0:
                raise Exception(f"WS 로그인 실패: {login_data.get('return_msg')}")

            # ✅ 로그인 후 잠시 대기
            await asyncio.sleep(0.5)

            # 2. 요청
            await ws.send(json.dumps(send_data))

            # ✅ 요청한 TRNM 응답 올 때까지 루프
            while True:
                resp = json.loads(
                    await asyncio.wait_for(ws.recv(), timeout=timeout)
                )
                if resp.get("trnm") == send_data.get("trnm"):
                    return resp
                self.log(f"WS 수신 (무시): {resp.get('trnm')}")

        return {}

    # ── ka10172: 조건검색 종목 조회 ──────────────────────
    async def _fetch_condition_stocks(self):
        """
        문서 기준: 같은 WS 세션에서
        LOGIN → CNSRLST → CNSRREQ 순서로 호출
        """
        seq = str(self.settings.get("condition_seq", ""))

        headers = {
            "authorization": f"Bearer {self.api.access_token}",
            "appkey": self.api.app_key,
            "secretkey": self.api.secret_key,
        }

        async with websockets.connect(
                self.WS_URL, additional_headers=headers
        ) as ws:
            # 1. 로그인
            await ws.send(json.dumps({
                "trnm": "LOGIN",
                "token": self.api.access_token,
            }))
            login_data = json.loads(
                await asyncio.wait_for(ws.recv(), timeout=30)
            )
            if login_data.get("return_code") != 0:
                raise Exception(f"WS 로그인 실패: {login_data.get('return_msg')}")
            await asyncio.sleep(0.5)

            # ✅ 2. CNSRLST 먼저 (필수!)
            await ws.send(json.dumps({"trnm": "CNSRLST"}))
            lst_resp = json.loads(
                await asyncio.wait_for(ws.recv(), timeout=30)
            )
            self.log(f"CNSRLST 응답: {lst_resp.get('return_code')} {lst_resp.get('return_msg', '')}")
            if lst_resp.get("return_code") != 0:
                raise Exception(f"CNSRLST 실패: {lst_resp.get('return_msg')}")
            await asyncio.sleep(0.3)

            # ✅ 3. CNSRREQ (종목 조회)
            await ws.send(json.dumps({
                "trnm": "CNSRREQ",
                "seq": seq,
                "search_type": "0",
                "stex_tp": "K",
                "cont_yn": "N",
                "next_key": "",
            }))

            # CNSRREQ 응답 대기
            while True:
                resp = json.loads(
                    await asyncio.wait_for(ws.recv(), timeout=30)
                )
                trnm = resp.get("trnm", "")
                self.log(f"WS 수신: trnm={trnm} code={resp.get('return_code')}")
                if trnm == "CNSRREQ":
                    break

        if resp.get("return_code") != 0:
            self.log(f"조건검색 실패: {resp.get('return_msg')}")
            return []

        stocks = resp.get("data", [])[:5]
        result = []
        for s in stocks:
            try:
                code = s.get("9001", "").lstrip("A")
                name_ = s.get("302", "")
                price = abs(int(str(s.get("10", "0")).lstrip("0") or "0"))
                rate = float(str(s.get("12", "0")).replace("+", "").lstrip("0") or "0")
                if not code:
                    continue
                result.append({
                    "code": code,
                    "name": name_,
                    "price": price,
                    "rate": rate,
                })
            except Exception as e:
                self.log(f"종목 파싱 오류: {e}")
                continue
        return result

    # ── 매수 판단 ────────────────────────────────────────
    def check_buy(self, pos: TradePosition, cur_price: int):
        buy_pct  = self.settings.get("buy_pct", 1.0)
        max_buy  = self.settings.get("max_buy", 10)
        stop_pct = self.settings.get("buy_stop_pct", 30)

        drop_from_base = (pos.base_price - cur_price) / pos.base_price * 100
        if drop_from_base >= stop_pct:
            return

        level = int(drop_from_base / buy_pct)
        if level < 1 or level > max_buy:
            return
        if level in pos.buy_levels:
            return

        buy_qty = 3 if level == 1 else 1
        pos.buy_levels.add(level)
        result = self.api.buy(pos.stock_code, buy_qty)
        if result.get("return_code") == 0:
            pos.update_avg(cur_price, buy_qty)
            insert_trade(
                pos.stock_code, pos.stock_name, "매수",
                buy_qty, cur_price, pos.avg_price, 0, 0.0, self.account_no
            )
            self.log(
                f"매수: {pos.stock_name}({pos.stock_code}) "
                f"{cur_price:,}원 x {buy_qty}주 "
                f"| 평단: {pos.avg_price:,}원 "
                f"| 보유: {pos.quantity}주 "
                f"| 기준가 대비 -{drop_from_base:.1f}%"
            )
        else:
            pos.buy_levels.discard(level)
            self.log(f"매수 실패: {result.get('return_msg')}")

    # ── 매도 판단 ────────────────────────────────────────
    def check_sell(self, pos: TradePosition, cur_price: int):
        if pos.quantity <= 0 or pos.avg_price <= 0:
            return

        sell_pct      = self.settings.get("sell_pct", 1.0)
        all_sell_pct  = self.settings.get("all_sell_pct", 20.0)
        rise_from_avg = (cur_price - pos.avg_price) / pos.avg_price * 100

        if rise_from_avg >= all_sell_pct:
            qty    = pos.quantity
            result = self.api.sell(pos.stock_code, qty)
            if result.get("return_code") == 0:
                pnl      = (cur_price - pos.avg_price) * qty
                pnl_rate = round(rise_from_avg, 2)
                insert_trade(
                    pos.stock_code, pos.stock_name, "전량매도",
                    qty, cur_price, pos.avg_price, pnl, pnl_rate, self.account_no
                )
                self.log(
                    f"전량 청산: {pos.stock_name} {cur_price:,}원 x {qty}주 "
                    f"| 평단 대비 +{pnl_rate:.2f}% | 손익: {pnl:+,}원"
                )
                pos.quantity   = 0
                pos.total_cost = 0
                pos.sell_levels.clear()
            else:
                self.log(f"전량 매도 실패: {result.get('return_msg')}")
            return

        level = int(rise_from_avg / sell_pct)
        if level < 1 or level in pos.sell_levels:
            return

        pos.sell_levels.add(level)
        result = self.api.sell(pos.stock_code, 1)
        if result.get("return_code") == 0:
            pnl      = (cur_price - pos.avg_price)
            pnl_rate = round(rise_from_avg, 2)
            insert_trade(
                pos.stock_code, pos.stock_name, "분할매도",
                1, cur_price, pos.avg_price, pnl, pnl_rate, self.account_no
            )
            pos.reduce_qty(1)
            self.log(
                f"분할 매도: {pos.stock_name} {cur_price:,}원 x 1주 "
                f"| 평단 대비 +{pnl_rate:.2f}% "
                f"| 손익: {pnl:+,}원 | 잔여: {pos.quantity}주"
            )
        else:
            pos.sell_levels.discard(level)
            self.log(f"분할 매도 실패: {result.get('return_msg')}")

    # ── 손절 판단 ────────────────────────────────────────
    def check_stop_loss(self, pos: TradePosition, cur_price: int):
        if pos.quantity <= 0 or pos.avg_price <= 0:
            return

        stop_loss_pct  = self.settings.get("stop_loss_pct", 15.0)
        drop_from_base = (pos.base_price - cur_price) / pos.base_price * 100

        if drop_from_base >= stop_loss_pct:
            qty    = pos.quantity
            result = self.api.sell(pos.stock_code, qty)
            if result.get("return_code") == 0:
                pnl      = (cur_price - pos.avg_price) * qty
                pnl_rate = round(
                    (cur_price - pos.avg_price) / pos.avg_price * 100, 2
                )
                insert_trade(
                    pos.stock_code, pos.stock_name, "손절",
                    qty, cur_price, pos.avg_price, pnl, pnl_rate, self.account_no
                )
                self.log(
                    f"⚠️ 손절: {pos.stock_name} {cur_price:,}원 x {qty}주 "
                    f"| 기준가 대비 -{drop_from_base:.1f}% | 손익: {pnl:+,}원"
                )
                pos.quantity   = 0
                pos.total_cost = 0
                pos.buy_levels.clear()
                pos.sell_levels.clear()
            else:
                self.log(f"손절 실패: {result.get('return_msg')}")

    # ── 메인 루프 ────────────────────────────────────────
    def _run_loop(self):
        interval        = self.settings.get("interval_sec", 60)
        select_interval = self.settings.get("select_min", 30) * 60

        self.log(f"자동매매 시작! (계좌: {self.account_no})")
        self.status_signal.emit("실행중")
        last_select = 0

        while self.running:
            now = time.time()

            if now - last_select >= select_interval or last_select == 0:
                self.log("조건검색으로 종목 선정 중...")
                stocks    = self.fetch_top5_stocks()
                new_codes = {s["code"] for s in stocks}

                for code in list(self.positions.keys()):
                    pos = self.positions[code]
                    if code not in new_codes and pos.quantity == 0:
                        del self.positions[code]
                        self.log(f"포지션 제거 (조건 이탈): {code}")

                for s in stocks:
                    code = s["code"]
                    if code not in self.positions:
                        self.positions[code] = TradePosition(
                            code, s["name"], s["price"]
                        )
                        self.log(
                            f"종목 선정: {s['name']}({code}) "
                            f"기준가 {s['price']:,}원"
                        )
                last_select = now

            for code, pos in list(self.positions.items()):
                try:
                    data = self.api.get_current_price(code)
                    if data.get("return_code") != 0:
                        continue
                    cur_price = abs(int(str(data.get("cur_prc", 0))
                                    .replace("+","").replace("-","").replace(",","")))
                    self.price_signal.emit(code, cur_price)
                    self.check_stop_loss(pos, cur_price)
                    self.check_sell(pos, cur_price)
                    self.check_buy(pos, cur_price)
                except Exception as e:
                    self.log(f"오류({code}): {e}")

            time.sleep(interval)

        self.log("자동매매 중지됨")
        self.status_signal.emit("대기중")

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False