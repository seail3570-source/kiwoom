# main.py
import sys
import asyncio
import json
import websockets
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTextEdit, QGroupBox, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QSpinBox, QDoubleSpinBox, QCheckBox, QComboBox,
    QFrame, QScrollArea
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor
import pyqtgraph as pg

from kiwoom_api import KiwoomREST
from trader import AutoTrader
from db import init_db, get_trades, get_pnl_summary, save_setting, load_setting
from config import APP_KEY, SECRET_KEY

C_RED    = "#c0392b"
C_BLUE   = "#2980b9"
C_GREEN  = "#27ae60"
C_CARD   = "#ffffff"
C_BORDER = "#dee2e6"


# ── WebSocket 작업을 별도 스레드에서 실행 ────────────────
class WSWorker(QThread):
    finished = pyqtSignal(object)
    error    = pyqtSignal(str)

    def __init__(self, coro_fn):
        super().__init__()
        self.coro_fn = coro_fn

    def run(self):
        try:
            loop   = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(self.coro_fn())
            loop.close()
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


def card_widget(title=""):
    box = QGroupBox(title)
    box.setStyleSheet(f"""
        QGroupBox {{
            background:{C_CARD};
            border:1px solid {C_BORDER};
            border-radius:8px;
            margin-top:6px;
            font-size:12px;
            font-weight:bold;
        }}
        QGroupBox::title {{
            subcontrol-origin:margin;
            left:10px;
            color:#2c3e50;
        }}
    """)
    return box


# ══════════════════════════════════════════════════════════
# 대시보드 탭
# ══════════════════════════════════════════════════════════
class DashboardTab(QWidget):
    def __init__(self, api, parent=None):
        super().__init__(parent)
        self.api = api
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        summary = QHBoxLayout()
        self.lbl_evlt     = self._metric_card("총평가금액", "- 원", summary)
        self.lbl_pnl      = self._metric_card("총평가손익", "- 원", summary)
        self.lbl_pnl_rate = self._metric_card("수익률",     "- %",  summary)
        self.lbl_deposit  = self._metric_card("예수금",     "- 원", summary)
        layout.addLayout(summary)

        chart_box = card_widget("수익률 차트")
        chart_layout = QVBoxLayout(chart_box)
        period_row = QHBoxLayout()
        self.period_btns = {}
        for p, t in [("day","일별"),("week","주별"),("month","월별"),("year","연별")]:
            btn = QPushButton(t)
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setStyleSheet("""
                QPushButton {
                    border:1px solid #bdc3c7; border-radius:4px;
                    padding:0 12px; background:#fff;
                    color:#2c3e50; font-size:12px;
                }
                QPushButton:checked {
                    background:#2980b9; color:white; border-color:#2980b9;
                }
            """)
            btn.clicked.connect(lambda _, period=p: self._load_chart(period))
            period_row.addWidget(btn)
            self.period_btns[p] = btn
        period_row.addStretch()
        chart_layout.addLayout(period_row)

        self.chart = pg.PlotWidget()
        self.chart.setBackground("white")
        self.chart.setMinimumHeight(180)
        self.chart.showGrid(x=True, y=True, alpha=0.3)
        self.chart.setLabel("left", "손익(원)")
        chart_layout.addWidget(self.chart)
        layout.addWidget(chart_box)

        stats_box = card_widget("기간별 손익 통계")
        stats_layout = QGridLayout(stats_box)
        for i, h in enumerate(["기간","총거래","총손익","승/패","승률"]):
            l = QLabel(h)
            l.setAlignment(Qt.AlignCenter)
            l.setStyleSheet("font-size:11px;color:#7f8c8d;font-weight:bold;")
            stats_layout.addWidget(l, 0, i)

        self.stat_rows = {}
        for row, (p, t) in enumerate([
            ("day","오늘"), ("week","이번 주"),
            ("month","이번 달"), ("year","올해")
        ], 1):
            lp = QLabel(t)
            lp.setAlignment(Qt.AlignCenter)
            stats_layout.addWidget(lp, row, 0)
            cells = []
            for col in range(1, 5):
                l = QLabel("-")
                l.setAlignment(Qt.AlignCenter)
                l.setStyleSheet("font-size:12px;")
                stats_layout.addWidget(l, row, col)
                cells.append(l)
            self.stat_rows[p] = cells
        layout.addWidget(stats_box)

        trade_box = card_widget("최근 거래 내역")
        trade_layout = QVBoxLayout(trade_box)
        self.trade_table = QTableWidget(0, 7)
        self.trade_table.setHorizontalHeaderLabels(
            ["시간","종목","구분","수량","체결가","평단가","손익"]
        )
        self.trade_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.trade_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.trade_table.setAlternatingRowColors(True)
        self.trade_table.setStyleSheet("font-size:12px;")
        trade_layout.addWidget(self.trade_table)
        layout.addWidget(trade_box)

        self.period_btns["day"].setChecked(True)
        self._load_chart("day")
        self._load_stats()
        self._load_trades()

    def _metric_card(self, title, value, parent_layout):
        box = QFrame()
        box.setStyleSheet(f"""
            QFrame {{
                background:{C_CARD}; border:1px solid {C_BORDER};
                border-radius:8px; padding:4px;
            }}
        """)
        v = QVBoxLayout(box)
        v.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size:11px;color:#7f8c8d;")
        v.addWidget(t)
        val = QLabel(value)
        val.setStyleSheet("font-size:16px;font-weight:bold;color:#2c3e50;")
        v.addWidget(val)
        parent_layout.addWidget(box)
        return val

    def _load_chart(self, period):
        for p, btn in self.period_btns.items():
            btn.setChecked(p == period)
        trades = get_trades(period)
        sells  = [t for t in trades if t["order_type"] == "매도"]
        self.chart.clear()
        if not sells:
            return
        pnls   = [t["pnl"] for t in sells]
        x      = list(range(len(pnls)))
        colors = [C_GREEN if p >= 0 else C_RED for p in pnls]
        bars   = pg.BarGraphItem(
            x=x, height=pnls, width=0.6,
            brushes=[pg.mkBrush(c) for c in colors]
        )
        self.chart.addItem(bars)
        cumulative, s = [], 0
        for p in pnls:
            s += p
            cumulative.append(s)
        self.chart.plot(x, cumulative, pen=pg.mkPen(color=C_BLUE, width=2))

    def _load_stats(self):
        for period, cells in self.stat_rows.items():
            s     = get_pnl_summary(period)
            pnl   = s["total_pnl"]
            color = C_GREEN if pnl >= 0 else C_RED
            cells[0].setText(str(s["total_trades"]))
            cells[1].setText(f"{pnl:+,}원")
            cells[1].setStyleSheet(f"font-size:12px;color:{color};font-weight:bold;")
            cells[2].setText(f"{s['win_count']}승 {s['lose_count']}패")
            cells[3].setText(f"{s['win_rate']}%")

    def _load_trades(self):
        trades = get_trades("day")
        self.trade_table.setRowCount(0)
        for t in trades[:30]:
            row = self.trade_table.rowCount()
            self.trade_table.insertRow(row)
            vals = [
                t["dt"][11:19],
                f"{t['stock_name']}({t['stock_code']})",
                t["order_type"],
                str(t["quantity"]),
                f"{t['price']:,}",
                f"{t['avg_price']:,}",
                f"{t['pnl']:+,}" if t["order_type"] == "매도" else "-",
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if col == 2:
                    item.setForeground(QColor(C_RED if val == "매수" else C_GREEN))
                if col == 6 and val != "-":
                    item.setForeground(QColor(C_GREEN if t["pnl"] >= 0 else C_RED))
                self.trade_table.setItem(row, col, item)

    def refresh(self, balance_data=None):
        if balance_data and balance_data.get("return_code") == 0:
            def safe_int(val):
                try:
                    return int(str(val).strip().lstrip("0") or "0")
                except:
                    return 0
            def safe_float(val):
                try:
                    return float(str(val).strip() or "0")
                except:
                    return 0.0

            evlt  = safe_int(balance_data.get("tot_evlt_amt",       "0"))
            pnl   = safe_int(balance_data.get("tot_evlt_pl",        "0"))
            rate  = safe_float(balance_data.get("tot_prft_rt",      "0"))
            dep   = safe_int(balance_data.get("prsm_dpst_aset_amt", "0"))
            color = C_GREEN if pnl >= 0 else C_RED
            self.lbl_evlt.setText(f"{evlt:,} 원")
            self.lbl_pnl.setText(f"{pnl:+,} 원")
            self.lbl_pnl.setStyleSheet(f"font-size:16px;font-weight:bold;color:{color};")
            self.lbl_pnl_rate.setText(f"{rate:+.2f} %")
            self.lbl_pnl_rate.setStyleSheet(f"font-size:16px;font-weight:bold;color:{color};")
            self.lbl_deposit.setText(f"{dep:,} 원")
        self._load_stats()
        self._load_trades()


# ══════════════════════════════════════════════════════════
# 자동매매 설정 탭
# ══════════════════════════════════════════════════════════
class SettingsTab(QWidget):
    settings_saved = pyqtSignal(dict)

    def __init__(self, api: KiwoomREST, parent=None):
        super().__init__(parent)
        self.api        = api
        self.conditions = []
        self._ws_worker  = None
        self._ws_worker2 = None
        self._build()
        self._load()

    def _get_ws_url(self):
        return "wss://mockapi.kiwoom.com:10000/api/dostk/websocket" \
               if self.api.is_mock else \
               "wss://api.kiwoom.com:10000/api/dostk/websocket"

    def _build(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner  = QWidget()
        layout = QVBoxLayout(inner)
        layout.setSpacing(12)
        outer  = QVBoxLayout(self)
        outer.addWidget(scroll)
        scroll.setWidget(inner)

        # ── 1단계: 조건검색식 선택 ────────────────────────
        box1 = card_widget("1단계 — 조건검색식 선택 (ka10171)")
        g1   = QGridLayout(box1)
        g1.setColumnStretch(1, 1)

        g1.addWidget(QLabel("조건검색식"), 0, 0)
        self.cmb_condition = QComboBox()
        self.cmb_condition.setMinimumWidth(250)
        self.cmb_condition.setPlaceholderText("조건검색식을 불러오세요")
        self.cmb_condition.currentIndexChanged.connect(self._on_condition_changed)
        g1.addWidget(self.cmb_condition, 0, 1)

        btn_load = QPushButton("조건검색식 불러오기")
        btn_load.setStyleSheet(
            "background:#2980b9;color:white;"
            "border-radius:4px;padding:5px 12px;font-size:12px;"
        )
        btn_load.clicked.connect(self._load_conditions)
        g1.addWidget(btn_load, 0, 2)

        self.lbl_condition_status = QLabel("영웅문4에서 조건검색식을 먼저 만들어주세요")
        self.lbl_condition_status.setStyleSheet("font-size:11px;color:#e74c3c;")
        g1.addWidget(self.lbl_condition_status, 1, 0, 1, 3)

        g1.addWidget(QLabel("선정 주기 (분)"), 2, 0)
        self.spin_select = QSpinBox()
        self.spin_select.setRange(5, 120)
        self.spin_select.setValue(30)
        self.spin_select.setSuffix(" 분마다 재선정")
        g1.addWidget(self.spin_select, 2, 1)
        layout.addWidget(box1)

        # ── 조건검색 결과 종목 테이블 ─────────────────────
        box1b = card_widget("조건검색 결과 종목")
        box1b_layout = QVBoxLayout(box1b)
        self.condition_table = QTableWidget(0, 5)
        self.condition_table.setHorizontalHeaderLabels(
            ["종목코드", "종목명", "현재가", "등락률", "기준가"]
        )
        self.condition_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.condition_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.condition_table.setAlternatingRowColors(True)
        self.condition_table.setMaximumHeight(180)
        self.condition_table.setStyleSheet("font-size:12px;")
        box1b_layout.addWidget(self.condition_table)
        layout.addWidget(box1b)

        # ── 2단계: 매수 조건 ──────────────────────────────
        box2 = card_widget("2단계 — 매수 조건 (기준가 대비 분할매수)")
        g2   = QGridLayout(box2)
        g2.setColumnStretch(1, 1)

        g2.addWidget(QLabel("기준가 설정"), 0, 0)
        self.cmb_base = QComboBox()
        self.cmb_base.addItems(["조건검색 진입 시점 가격", "당일 시가"])
        g2.addWidget(self.cmb_base, 0, 1)

        g2.addWidget(QLabel("매수 트리거 (%)"), 1, 0)
        self.spin_buy = QDoubleSpinBox()
        self.spin_buy.setRange(0.1, 10)
        self.spin_buy.setValue(1.0)
        self.spin_buy.setSuffix(" % 하락마다 1주 매수")
        g2.addWidget(self.spin_buy, 1, 1)

        g2.addWidget(QLabel("최대 매수 횟수"), 2, 0)
        self.spin_maxbuy = QSpinBox()
        self.spin_maxbuy.setRange(1, 50)
        self.spin_maxbuy.setValue(10)
        self.spin_maxbuy.setSuffix(" 회")
        g2.addWidget(self.spin_maxbuy, 2, 1)

        g2.addWidget(QLabel("매수 중단 하한 (%)"), 3, 0)
        self.spin_buystop = QDoubleSpinBox()
        self.spin_buystop.setRange(10, 50)
        self.spin_buystop.setValue(30)
        self.spin_buystop.setSuffix(" % 이상 하락 시 중단")
        g2.addWidget(self.spin_buystop, 3, 1)
        layout.addWidget(box2)

        # ── 3단계: 매도 조건 ──────────────────────────────
        box3 = card_widget("3단계 — 매도 조건 (평단가 기준 분할매도)")
        g3   = QGridLayout(box3)
        g3.setColumnStretch(1, 1)

        g3.addWidget(QLabel("분할매도 트리거 (%)"), 0, 0)
        self.spin_sell = QDoubleSpinBox()
        self.spin_sell.setRange(0.1, 10)
        self.spin_sell.setValue(1.0)
        self.spin_sell.setSuffix(" % 상승마다 1주 매도")
        g3.addWidget(self.spin_sell, 0, 1)

        g3.addWidget(QLabel("전량 청산 조건 (%)"), 1, 0)
        self.spin_allsell = QDoubleSpinBox()
        self.spin_allsell.setRange(5, 50)
        self.spin_allsell.setValue(20.0)
        self.spin_allsell.setSuffix(" % 이상 상승 시 전량 매도")
        g3.addWidget(self.spin_allsell, 1, 1)

        g3.addWidget(QLabel("장 마감 전 청산"), 2, 0)
        self.chk_close = QCheckBox("15:20 이후 전량 매도")
        g3.addWidget(self.chk_close, 2, 1)

        g3.addWidget(QLabel("손절 안전장치 (%)"), 3, 0)
        self.spin_stoploss = QDoubleSpinBox()
        self.spin_stoploss.setRange(10, 60)
        self.spin_stoploss.setValue(35)
        self.spin_stoploss.setSuffix(" % 이상 하락 시 전량 손절")
        g3.addWidget(self.spin_stoploss, 3, 1)
        layout.addWidget(box3)

        # ── 기타 설정 ──────────────────────────────────────
        box4 = card_widget("기타 설정")
        g4   = QGridLayout(box4)
        g4.addWidget(QLabel("가격 조회 주기 (초)"), 0, 0)
        self.spin_interval = QSpinBox()
        self.spin_interval.setRange(10, 300)
        self.spin_interval.setValue(60)
        self.spin_interval.setSuffix(" 초")
        g4.addWidget(self.spin_interval, 0, 1)
        layout.addWidget(box4)
        layout.addStretch()

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_reset = QPushButton("초기화")
        btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(btn_reset)
        btn_save = QPushButton("설정 저장")
        btn_save.setStyleSheet(
            "background:#2980b9;color:white;"
            "border-radius:4px;padding:6px 20px;font-size:13px;"
        )
        btn_save.clicked.connect(self._save)
        btn_row.addWidget(btn_save)
        layout.addLayout(btn_row)

    # ── 조건검색식 목록 불러오기 (QThread) ───────────────
    def _load_conditions(self):
        self.lbl_condition_status.setText("조건검색식 불러오는 중...")
        self.lbl_condition_status.setStyleSheet("font-size:11px;color:#f39c12;")

        ws_url = self._get_ws_url()
        token  = self.api.access_token
        app_key    = self.api.app_key
        secret_key = self.api.secret_key

        async def fetch():
            try:
                async with websockets.connect(
                    ws_url,
                    additional_headers={
                        "authorization": f"Bearer {token}",
                        "appkey":        app_key,
                        "secretkey":     secret_key,
                    }
                ) as ws:
                    await ws.send(json.dumps({
                        "trnm":  "LOGIN",
                        "token": token,
                    }))
                    login_data = json.loads(
                        await asyncio.wait_for(ws.recv(), timeout=30)
                    )
                    if login_data.get("return_code") != 0:
                        return {"ok": False, "msg": f"로그인 실패: {login_data.get('return_msg')}"}

                    await asyncio.sleep(0.5)

                    await ws.send(json.dumps({"trnm": "CNSRLST"}))
                    data = json.loads(
                        await asyncio.wait_for(ws.recv(), timeout=30)
                    )
                    if data.get("return_code") != 0:
                        return {"ok": False, "msg": data.get("return_msg")}

                    raw = data.get("data", [])
                    conditions = [
                        {"seq": r[0], "name": r[1]}
                        for r in raw if isinstance(r, list) and len(r) >= 2
                    ]
                    return {"ok": True, "conditions": conditions}
            except Exception as e:
                return {"ok": False, "msg": str(e)}

        self._ws_worker = WSWorker(fetch)
        self._ws_worker.finished.connect(self._on_conditions_loaded)
        self._ws_worker.error.connect(
            lambda e: self.lbl_condition_status.setText(f"❌ {e}")
        )
        self._ws_worker.start()

    def _on_conditions_loaded(self, result):
        self.cmb_condition.blockSignals(True)
        self.cmb_condition.clear()

        if result.get("ok"):
            conditions = result["conditions"]
            self.conditions = conditions
            for c in conditions:
                self.cmb_condition.addItem(
                    f"[{c.get('seq')}] {c.get('name')}", userData=c
                )
            self.lbl_condition_status.setText(
                f"✅ {len(conditions)}개 조건검색식 로드 완료"
            )
            self.lbl_condition_status.setStyleSheet("font-size:11px;color:#27ae60;")
        else:
            self.lbl_condition_status.setText(
                f"❌ {result.get('msg')} — 영웅문4에서 조건검색식을 먼저 만들어주세요"
            )
            self.lbl_condition_status.setStyleSheet("font-size:11px;color:#e74c3c;")

        self.cmb_condition.blockSignals(False)

        if result.get("ok") and result.get("conditions"):
            self._load_condition_stocks(result["conditions"][0])

    # ── 조건검색 종목 조회 (QThread) ─────────────────────
    def _on_condition_changed(self, index):
        selected = self.cmb_condition.itemData(index)
        if selected:
            self._load_condition_stocks(selected)

    def _load_condition_stocks(self, condition: dict):
        self.lbl_condition_status.setText("종목 조회 중...")
        self.lbl_condition_status.setStyleSheet("font-size:11px;color:#f39c12;")

        ws_url     = self._get_ws_url()
        token      = self.api.access_token
        app_key    = self.api.app_key
        secret_key = self.api.secret_key
        seq        = str(condition.get("seq", ""))

    async def fetch():
            import os
            log_file = os.path.join(os.path.dirname(__file__), "ws_debug.log")

            def wlog(msg):
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write(f"{datetime.now().strftime('%H:%M:%S')} {msg}\n")
                print(msg)  # 터미널에도 출력

            try:
                wlog("1. WebSocket 연결 시도...")
                async with websockets.connect(
                        ws_url,
                        additional_headers={
                            "authorization": f"Bearer {token}",
                            "appkey": app_key,
                            "secretkey": secret_key,
                        }
                ) as ws:
                    wlog("2. 연결 성공! LOGIN 요청...")
                    await ws.send(json.dumps({
                        "trnm": "LOGIN",
                        "token": token,
                    }))
                    login_data = json.loads(
                        await asyncio.wait_for(ws.recv(), timeout=30)
                    )
                    wlog(f"3. LOGIN 응답: {login_data}")
                    if login_data.get("return_code") != 0:
                        return {"ok": False, "stocks": [], "condition": condition}
                    await asyncio.sleep(0.5)

                    wlog("4. CNSRLST 요청...")
                    await ws.send(json.dumps({"trnm": "CNSRLST"}))
                    lst = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                    wlog(f"5. CNSRLST 응답: {lst}")
                    await asyncio.sleep(0.3)

                    wlog(f"6. CNSRREQ 요청 (seq={seq})...")
                    await ws.send(json.dumps({
                        "trnm": "CNSRREQ",
                        "seq": seq,
                        "search_type": "0",
                        "stex_tp": "K",
                        "cont_yn": "N",
                        "next_key": "",
                    }))

                    while True:
                        resp = json.loads(
                            await asyncio.wait_for(ws.recv(), timeout=30)
                        )
                        wlog(f"7. WS 수신: {resp}")
                        if resp.get("trnm") == "CNSRREQ":
                            stocks = resp.get("data", []) \
                                if resp.get("return_code") == 0 else []
                            wlog(f"8. 종목 {len(stocks)}개 수신 완료!")
                            return {"ok": True, "stocks": stocks, "condition": condition}

            except Exception as e:
                wlog(f"오류: {e}")
                import traceback
                wlog(traceback.format_exc())
                return {"ok": False, "stocks": [], "condition": condition}


    def _on_stocks_loaded(self, result):
        stocks    = result.get("stocks", [])
        condition = result.get("condition", {})

        self.condition_table.setRowCount(0)
        for s in stocks:
            try:
                row   = self.condition_table.rowCount()
                self.condition_table.insertRow(row)
                code  = s.get("9001", "").lstrip("A")
                name  = s.get("302",  "")
                price = int(str(s.get("10", "0")).lstrip("0") or "0")
                rate  = str(s.get("12", "0"))

                vals = [code, name, f"{price:,}원", f"{rate}%", f"{price:,}원"]
                for col, val in enumerate(vals):
                    item = QTableWidgetItem(val)
                    item.setTextAlignment(Qt.AlignCenter)
                    if col == 3:
                        try:
                            r = float(rate)
                            item.setForeground(QColor(C_GREEN if r >= 0 else C_RED))
                        except:
                            pass
                    self.condition_table.setItem(row, col, item)
            except Exception:
                continue

        self.lbl_condition_status.setText(
            f"✅ [{condition.get('name')}] 종목 {len(stocks)}개 조회 완료"
        )
        self.lbl_condition_status.setStyleSheet("font-size:11px;color:#27ae60;")

    def _get_settings(self):
        selected = self.cmb_condition.currentData()
        return {
            "condition_seq":  selected.get("seq")  if selected else None,
            "condition_name": selected.get("name") if selected else None,
            "select_min":     self.spin_select.value(),
            "base_type":      self.cmb_base.currentIndex(),
            "buy_pct":        self.spin_buy.value(),
            "max_buy":        self.spin_maxbuy.value(),
            "buy_stop_pct":   self.spin_buystop.value(),
            "sell_pct":       self.spin_sell.value(),
            "all_sell_pct":   self.spin_allsell.value(),
            "close_sell":     self.chk_close.isChecked(),
            "stop_loss_pct":  self.spin_stoploss.value(),
            "interval_sec":   self.spin_interval.value(),
        }

    def _save(self):
        s = self._get_settings()
        if not s.get("condition_seq"):
            self.lbl_condition_status.setText("❌ 조건검색식을 먼저 선택하세요!")
            self.lbl_condition_status.setStyleSheet("font-size:11px;color:#e74c3c;")
            return
        for k, v in s.items():
            save_setting(k, str(v) if v else "")
        self.settings_saved.emit(s)

    def _load(self):
        self.spin_buy.setValue(float(load_setting("buy_pct", 1.0)))
        self.spin_maxbuy.setValue(int(load_setting("max_buy", 10)))
        self.spin_sell.setValue(float(load_setting("sell_pct", 1.0)))
        self.spin_allsell.setValue(float(load_setting("all_sell_pct", 20.0)))
        self.spin_interval.setValue(int(load_setting("interval_sec", 60)))

    def _reset(self):
        self.spin_buy.setValue(1.0)
        self.spin_maxbuy.setValue(10)
        self.spin_sell.setValue(1.0)
        self.spin_allsell.setValue(20.0)
        self.spin_interval.setValue(60)
        self.cmb_condition.clear()
        self.conditions = []
        self.condition_table.setRowCount(0)
        self.lbl_condition_status.setText("영웅문4에서 조건검색식을 먼저 만들어주세요")
        self.lbl_condition_status.setStyleSheet("font-size:11px;color:#e74c3c;")


# ══════════════════════════════════════════════════════════
# 수동 주문 탭
# ══════════════════════════════════════════════════════════
class OrderTab(QWidget):
    def __init__(self, api, get_account_fn, log_fn, parent=None):
        super().__init__(parent)
        self.api            = api
        self.get_account_fn = get_account_fn
        self.log            = log_fn
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)

        box1 = card_widget("현재가 조회")
        g1   = QGridLayout(box1)
        g1.addWidget(QLabel("종목코드"), 0, 0)
        self.edit_code_q = QLineEdit("005930")
        g1.addWidget(self.edit_code_q, 0, 1)
        btn_q = QPushButton("조회")
        btn_q.clicked.connect(self._query_price)
        g1.addWidget(btn_q, 0, 2)
        self.lbl_price_result = QLabel("")
        self.lbl_price_result.setStyleSheet("font-size:14px;font-weight:bold;")
        g1.addWidget(self.lbl_price_result, 1, 0, 1, 3)
        layout.addWidget(box1)

        box2 = card_widget("수동 주문")
        g2   = QGridLayout(box2)
        g2.addWidget(QLabel("종목코드"), 0, 0)
        self.edit_code = QLineEdit("005930")
        g2.addWidget(self.edit_code, 0, 1)
        g2.addWidget(QLabel("수량"), 1, 0)
        self.spin_qty = QSpinBox()
        self.spin_qty.setRange(1, 10000)
        self.spin_qty.setValue(1)
        g2.addWidget(self.spin_qty, 1, 1)
        g2.addWidget(QLabel("가격 (0=시장가)"), 2, 0)
        self.spin_price = QSpinBox()
        self.spin_price.setRange(0, 9999999)
        self.spin_price.setValue(0)
        g2.addWidget(self.spin_price, 2, 1)

        btn_row = QHBoxLayout()
        btn_buy = QPushButton("매수")
        btn_buy.setStyleSheet(
            f"background:{C_GREEN};color:white;border-radius:4px;"
            "padding:8px;font-size:14px;font-weight:bold;"
        )
        btn_buy.clicked.connect(lambda: self._order("1"))
        btn_sell = QPushButton("매도")
        btn_sell.setStyleSheet(
            f"background:{C_RED};color:white;border-radius:4px;"
            "padding:8px;font-size:14px;font-weight:bold;"
        )
        btn_sell.clicked.connect(lambda: self._order("2"))
        btn_row.addWidget(btn_buy)
        btn_row.addWidget(btn_sell)
        g2.addLayout(btn_row, 3, 0, 1, 2)
        layout.addWidget(box2)
        layout.addStretch()

    def _query_price(self):
        code = self.edit_code_q.text().strip()
        data = self.api.get_current_price(code)
        if data.get("return_code") == 0:
            name  = data.get("stk_nm", "")
            price = data.get("cur_prc", "")
            rate  = data.get("flu_rt", "")
            color = C_GREEN if "+" in str(rate) else C_RED
            self.lbl_price_result.setText(f"{name}  {price}원  ({rate}%)")
            self.lbl_price_result.setStyleSheet(
                f"font-size:14px;font-weight:bold;color:{color};"
            )
        else:
            self.lbl_price_result.setText(f"조회 실패: {data.get('return_msg')}")

    def _order(self, order_type):
        acnt  = self.get_account_fn()
        code  = self.edit_code.text().strip()
        qty   = self.spin_qty.value()
        price = self.spin_price.value()
        label = "매수" if order_type == "1" else "매도"
        if not acnt:
            self.log("❌ 계좌를 먼저 선택하세요!")
            return
        data = self.api.send_order(acnt, code, order_type, qty, price)
        if data.get("return_code") == 0:
            self.log(
                f"{label} 주문 성공: [{acnt}] {code} {qty}주 "
                f"({'시장가' if price == 0 else f'{price:,}원'})"
            )
        else:
            self.log(f"{label} 주문 실패: {data.get('return_msg')}")


# ══════════════════════════════════════════════════════════
# 메인 윈도우
# ══════════════════════════════════════════════════════════
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.api        = KiwoomREST(APP_KEY, SECRET_KEY)
        self.trader     = None
        self.settings   = {}
        self.account_no = ""
        init_db()
        self._build()
        self._start_timer()

    def _build(self):
        self.setWindowTitle("키움 REST API 자동매매")
        self.setMinimumSize(1100, 780)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setSpacing(0)
        root.setContentsMargins(0, 0, 0, 0)

        toolbar = QFrame()
        toolbar.setFixedHeight(50)
        toolbar.setStyleSheet("background:#2c3e50;")
        tb = QHBoxLayout(toolbar)
        tb.setContentsMargins(16, 0, 16, 0)
        tb.setSpacing(10)

        title = QLabel("키움 REST API 자동매매")
        title.setStyleSheet("color:white;font-size:15px;font-weight:bold;")
        tb.addWidget(title)

        self.lbl_conn = QLabel("⬛ 미연결")
        self.lbl_conn.setStyleSheet("color:#bdc3c7;font-size:12px;")
        tb.addWidget(self.lbl_conn)

        self.lbl_token_exp = QLabel("")
        self.lbl_token_exp.setStyleSheet("color:#95a5a6;font-size:11px;")
        tb.addWidget(self.lbl_token_exp)

        tb.addStretch()

        self.lbl_acnt = QLabel("계좌:")
        self.lbl_acnt.setStyleSheet("color:#bdc3c7;font-size:12px;")
        tb.addWidget(self.lbl_acnt)

        self.cmb_acnt = QComboBox()
        self.cmb_acnt.setFixedWidth(200)
        self.cmb_acnt.setStyleSheet("""
            QComboBox {
                background:#34495e; color:white;
                border:1px solid #5d6d7e; border-radius:4px;
                padding:3px 8px; font-size:12px;
            }
            QComboBox::drop-down { border:none; }
            QComboBox QAbstractItemView {
                background:#2c3e50; color:white;
                selection-background-color:#2980b9;
            }
        """)
        self.cmb_acnt.setEnabled(False)
        self.cmb_acnt.currentIndexChanged.connect(self._on_account_changed)
        tb.addWidget(self.cmb_acnt)

        self.lbl_run_status = QLabel("● 대기중")
        self.lbl_run_status.setStyleSheet("color:#f39c12;font-size:12px;font-weight:bold;")
        tb.addWidget(self.lbl_run_status)

        self.btn_mode = QPushButton("🟡 모의투자")
        self.btn_mode.setCheckable(True)
        self.btn_mode.setChecked(False)
        self.btn_mode.setFixedWidth(100)
        self.btn_mode.setStyleSheet("""
            QPushButton {
                color:white; background:#8e44ad; border:none;
                border-radius:4px; padding:6px 10px; font-size:12px;
            }
            QPushButton:checked { background:#f39c12; }
        """)
        self.btn_mode.clicked.connect(self._toggle_mode)
        tb.addWidget(self.btn_mode)

        btn_login = QPushButton("🔑 로그인")
        btn_login.setStyleSheet(
            "color:white;background:#27ae60;border:none;"
            "border-radius:4px;padding:6px 14px;font-size:12px;"
        )
        btn_login.clicked.connect(self._login)
        tb.addWidget(btn_login)

        self.btn_auto = QPushButton("▶ 자동매매 시작")
        self.btn_auto.setStyleSheet(
            "color:white;background:#2980b9;border:none;"
            "border-radius:4px;padding:6px 14px;font-size:12px;"
        )
        self.btn_auto.clicked.connect(self._toggle_auto)
        self.btn_auto.setEnabled(False)
        tb.addWidget(self.btn_auto)

        root.addWidget(toolbar)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane { border:none; background:#f8f9fa; }
            QTabBar::tab {
                padding:8px 20px; font-size:13px;
                border-bottom:2px solid transparent;
                background:#f8f9fa; color:#7f8c8d;
            }
            QTabBar::tab:selected {
                color:#2c3e50; border-bottom:2px solid #2980b9; font-weight:bold;
            }
        """)

        self.dash_tab  = DashboardTab(self.api)
        self.order_tab = OrderTab(self.api, self._get_account, self._log)
        self.set_tab   = SettingsTab(self.api)
        self.set_tab.settings_saved.connect(self._on_settings_saved)

        self.tabs.addTab(self.dash_tab,  "대시보드")
        self.tabs.addTab(self.order_tab, "수동 주문")
        self.tabs.addTab(self.set_tab,   "자동매매 설정")

        root.addWidget(self.tabs)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        self.log_box.setFont(QFont("Consolas", 9))
        self.log_box.setStyleSheet(
            "background:#1e272e;color:#dfe6e9;border:none;padding:4px;"
        )
        root.addWidget(self.log_box)

    def _get_account(self):
        return self.account_no

    def _start_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self._on_timer)
        self.timer.start(60000)

    def _on_timer(self):
        if self.api.access_token and self.account_no:
            try:
                data = self.api.get_balance(self.account_no)
                self.dash_tab.refresh(data)
            except Exception:
                pass

    def _login(self):
        try:
            self.api.get_token()
            if self.api.is_mock:
                self.lbl_conn.setText("🟡 연결됨 (모의투자)")
                self.lbl_conn.setStyleSheet("color:#f39c12;font-size:12px;")
                self._log("로그인 성공! (모의투자 서버)")
            else:
                self.lbl_conn.setText("🟢 연결됨 (실투자)")
                self.lbl_conn.setStyleSheet("color:#2ecc71;font-size:12px;")
                self._log("로그인 성공! (실투자 서버)")
            self.lbl_token_exp.setText(f"토큰 만료: {self.api.token_expires}")
            self._load_account_list()
        except Exception as e:
            self._log(f"로그인 실패: {e}")

    def _toggle_mode(self):
        from config import (
            REAL_APP_KEY, REAL_SECRET_KEY,
            MOCK_APP_KEY, MOCK_SECRET_KEY
        )
        is_mock = self.btn_mode.isChecked()
        if is_mock:
            self.api = KiwoomREST(MOCK_APP_KEY, MOCK_SECRET_KEY)
            self.api.BASE_URL = "https://mockapi.kiwoom.com"
            self.api.WS_URL   = "wss://mockapi.kiwoom.com:10000/api/dostk/websocket"
            self.api.is_mock  = True
            self.btn_mode.setText("🟡 모의투자")
            self._log("⚠️ 모의투자 서버로 변경 — 다시 로그인하세요!")
        else:
            self.api = KiwoomREST(REAL_APP_KEY, REAL_SECRET_KEY)
            self.api.BASE_URL = "https://api.kiwoom.com"
            self.api.WS_URL   = "wss://api.kiwoom.com:10000/api/dostk/websocket"
            self.api.is_mock  = False
            self.btn_mode.setText("🟢 실투자")
            self._log("✅ 실투자 서버로 변경 — 다시 로그인하세요!")

        self.api.access_token = None
        self.lbl_conn.setText("⬛ 미연결")
        self.lbl_conn.setStyleSheet("color:#bdc3c7;font-size:12px;")
        self.lbl_token_exp.setText("")
        self.btn_auto.setEnabled(False)
        self.cmb_acnt.setEnabled(False)
        self.cmb_acnt.clear()
        self.account_no    = ""
        self.dash_tab.api  = self.api
        self.order_tab.api = self.api
        self.set_tab.api   = self.api

    def _load_account_list(self):
        try:
            data     = self.api.get_account_list()
            acct_str = data.get("acctNo", "")
            accounts = [a.strip() for a in acct_str.split() if a.strip()]

            from config import MANUAL_ACCOUNTS
            for a in MANUAL_ACCOUNTS:
                if a not in accounts:
                    accounts.append(a)

            if not accounts:
                self._log("계좌 정보가 없습니다.")
                return

            self.cmb_acnt.blockSignals(True)
            self.cmb_acnt.clear()
            for no in accounts:
                self.cmb_acnt.addItem(no, userData=no)
            self.cmb_acnt.blockSignals(False)
            self.cmb_acnt.setEnabled(True)
            self.btn_auto.setEnabled(True)

            self.account_no = accounts[0]
            self.cmb_acnt.setCurrentIndex(0)
            self._log(f"계좌 {len(accounts)}개 로드 완료 → [{self.account_no}] 선택됨")

            balance = self.api.get_balance(self.account_no)
            self.dash_tab.refresh(balance)

        except Exception as e:
            self._log(f"계좌 목록 조회 실패: {e}")

    def _on_account_changed(self, index):
        acnt_no = self.cmb_acnt.itemData(index)
        if not acnt_no or acnt_no == self.account_no:
            return
        self.account_no = acnt_no
        self._log(f"계좌 변경: [{acnt_no}]")
        try:
            balance = self.api.get_balance(self.account_no)
            self.dash_tab.refresh(balance)
        except Exception as e:
            self._log(f"잔고 조회 실패: {e}")

    def _toggle_auto(self):
        if self.trader and self.trader.running:
            self.trader.stop()
            self.btn_auto.setText("▶ 자동매매 시작")
            self.btn_auto.setStyleSheet(
                "color:white;background:#2980b9;border:none;"
                "border-radius:4px;padding:6px 14px;font-size:12px;"
            )
        else:
            if not self.account_no:
                self._log("❌ 계좌를 먼저 선택하세요!")
                return
            s = self.set_tab._get_settings()
            if not s.get("condition_seq"):
                self._log("❌ 자동매매 설정 탭에서 조건검색식을 먼저 선택하세요!")
                self.tabs.setCurrentWidget(self.set_tab)
                return
            self.trader = AutoTrader(self.api, self.account_no, s)
            self.trader.log_signal.connect(self._log)
            self.trader.status_signal.connect(self._on_status)
            self.trader.start()
            self.btn_auto.setText("■ 자동매매 중지")
            self.btn_auto.setStyleSheet(
                "color:white;background:#c0392b;border:none;"
                "border-radius:4px;padding:6px 14px;font-size:12px;"
            )

    def _on_status(self, status):
        if status == "실행중":
            self.lbl_run_status.setText("● 실행중")
            self.lbl_run_status.setStyleSheet("color:#2ecc71;font-size:12px;font-weight:bold;")
        else:
            self.lbl_run_status.setText("● 대기중")
            self.lbl_run_status.setStyleSheet("color:#f39c12;font-size:12px;font-weight:bold;")

    def _on_settings_saved(self, s):
        self.settings = s
        self._log("✅ 설정이 저장됐습니다.")

    def _log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_box.append(f"[{ts}] {msg}")
        self.log_box.verticalScrollBar().setValue(
            self.log_box.verticalScrollBar().maximum()
        )

    def closeEvent(self, event):
        if self.trader:
            self.trader.stop()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())