import time
import threading
from ib_insync import IB
import config


class IBConnection:
    MAX_RETRIES = 20  # 最多重连次数，避免无限循环
    # 只有这些错误能解释 connect() 失败；2157 等数据 farm warning 不应覆盖 TimeoutError。
    _CONNECT_ERROR_CODES = {326, 502, 504}

    def __init__(self, host=config.IB_HOST, port=config.IB_PORT,
                 client_id=config.IB_CLIENT_ID, timeout=config.IB_TIMEOUT, retry_interval=5):
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.timeout = timeout
        self.retry_interval = retry_interval
        self._should_reconnect = True
        self.last_api_error = ''
        # Error 326 会在 socket 关闭前通过 errorEvent 到达；若只等 connect() 抛出的
        # TimeoutError，会丢掉真正原因，诊断端只能显示空字符串。
        self.ib.errorEvent += self._on_api_error

    def _on_api_error(self, req_id, error_code, error_string, contract=None):
        if error_code in self._CONNECT_ERROR_CODES:
            self.last_api_error = f'IB Error {error_code} (reqId {req_id}): {error_string}'

    def _on_disconnected(self):
        if not self._should_reconnect:
            return
        print("\n连接已断开，正在尝试重连...")
        # 必须在新线程中重连：此回调由 ib_insync 事件循环线程触发，
        # 在其中直接调用 ib.connect() 会死锁自己的 event loop。
        threading.Thread(target=self._reconnect_loop, daemon=True).start()

    def _reconnect_loop(self):
        for attempt in range(1, self.MAX_RETRIES + 1):
            if self.ib.isConnected():
                return
            try:
                time.sleep(self.retry_interval)
                self.ib.connect(self.host, self.port, clientId=self.client_id)
                self.ib.RequestTimeout = self.timeout
                print(f"重连成功！（第 {attempt} 次尝试）")
                return
            except Exception as e:
                print(f"重连第 {attempt}/{self.MAX_RETRIES} 次失败：{e}")
        print("已达到最大重连次数，请检查 IB Gateway 是否正常运行")

    def connect(self):
        try:
            print(f"正在连接 IB Gateway ({self.host}:{self.port})...")
            self.last_api_error = ''
            self.ib.connect(self.host, self.port, clientId=self.client_id)
            self.ib.RequestTimeout = self.timeout
            self._should_reconnect = True
            self.ib.disconnectedEvent += self._on_disconnected
            print("连接成功！账户:", self.ib.managedAccounts())
            return self.ib
        except Exception as e:
            detail = self.last_api_error or f'{type(e).__name__}: {e}'
            print(f"连接失败：{detail}")
            print("请确认：1) IB Gateway 已启动  2) API 已开启  3) 端口正确")
            raise RuntimeError(detail) from e

    def disconnect(self):
        self._should_reconnect = False
        self.ib.disconnectedEvent -= self._on_disconnected
        self.ib.disconnect()
        print("已断开连接")
