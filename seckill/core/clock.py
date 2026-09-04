"""时间同步。

秒杀拼的是毫秒，本地时钟偏差一两秒就足以出局。

这里从 NTP 服务器取权威时间算出偏移量，UDP 123 被封时自动退回 HTTP Date 头。
"""
from __future__ import annotations

import socket
import struct
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

from ..utils.logger import get_logger

logger = get_logger(__name__)

_NTP_DELTA = 2_208_988_800  # 1900-01-01 与 1970-01-01 之间的秒数

#: 超出这个范围的校时样本视为无效（本机时钟差一天以内都还能接受）
_MAX_PLAUSIBLE_OFFSET = 86_400.0
#: 往返超过 5 秒的样本没有校时价值
_MAX_PLAUSIBLE_RTT = 5.0

DEFAULT_NTP_SERVERS = (
    "ntp.aliyun.com",
    "ntp.tencent.com",
    "cn.pool.ntp.org",
    "time.windows.com",
    "pool.ntp.org",
)

DEFAULT_HTTP_SOURCES = (
    "https://www.baidu.com",
    "https://www.taobao.com",
    "https://www.jd.com",
    "https://www.cloudflare.com",
)


@dataclass(slots=True)
class SyncResult:
    ok: bool
    offset_ms: float = 0.0
    source: str = ""
    rtt_ms: float = 0.0
    message: str = ""

    def __str__(self) -> str:
        if not self.ok:
            return f"校时失败：{self.message}"
        return f"已与 {self.source} 校时，偏差 {self.offset_ms:+.0f} ms（往返 {self.rtt_ms:.0f} ms）"


def _query_ntp(server: str, timeout: float = 1.5) -> tuple[float, float]:
    """返回 (offset_seconds, rtt_seconds)。失败抛异常。"""
    msg = b"\x1b" + 47 * b"\0"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        t0 = time.time()
        sock.sendto(msg, (server, 123))
        data, _ = sock.recvfrom(1024)
        t3 = time.time()
    if len(data) < 48:
        raise ValueError("NTP 响应过短")
    # 48 字节报文：32-39 是 Receive Timestamp，40-47 是 Transmit Timestamp，
    # 各占 8 字节（高 4 字节整秒自 1900 起，低 4 字节为 2^-32 秒的小数部分）
    recv_int, recv_frac = struct.unpack("!II", data[32:40])
    trans_int, trans_frac = struct.unpack("!II", data[40:48])
    t1 = (recv_int - _NTP_DELTA) + recv_frac / 2**32
    t2 = (trans_int - _NTP_DELTA) + trans_frac / 2**32
    offset = ((t1 - t0) + (t2 - t3)) / 2
    rtt = (t3 - t0) - (t2 - t1)
    # 解析或网络异常时可能产出离谱样本，直接作废
    if abs(offset) > _MAX_PLAUSIBLE_OFFSET or rtt < 0 or rtt > _MAX_PLAUSIBLE_RTT:
        raise ValueError(f"NTP 样本不合理（offset={offset:.1f}s rtt={rtt:.3f}s）")
    return offset, max(rtt, 0.0)


def _query_http(url: str, timeout: float = 2.0) -> tuple[float, float]:
    """用 HTTP Date 头估算偏移。精度不及 NTP，但能穿过只放通 80/443 的网络。"""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "SecKill/2.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 地址来自内置列表
        date_str = resp.headers.get("Date")
        t3 = time.time()
    if not date_str:
        raise ValueError("响应缺少 Date 头")
    server_time = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z").replace(
        tzinfo=timezone.utc
    )
    midpoint = (t0 + t3) / 2
    offset = server_time.timestamp() - midpoint
    return offset, t3 - t0


class Clock:
    """带偏移量校正的时钟。线程安全，可在任意线程调用 now()。"""

    def __init__(self) -> None:
        self._offset = 0.0
        self._lock = threading.Lock()
        self._synced_at: datetime | None = None
        self._source = ""
        self._rtt_ms = 0.0

    @property
    def synced(self) -> bool:
        return self._synced_at is not None

    @property
    def offset_ms(self) -> float:
        with self._lock:
            return self._offset * 1000

    @property
    def source(self) -> str:
        return self._source

    @property
    def synced_at(self) -> datetime | None:
        return self._synced_at

    def sync(
        self,
        servers: tuple[str, ...] = DEFAULT_NTP_SERVERS,
        timeout: float = 1.5,
        tries: int = 3,
    ) -> SyncResult:
        """依次尝试 NTP，取往返时延最小的样本；全失败则退回 HTTP。"""
        best: tuple[float, float, str] | None = None
        last_error = ""

        for server in servers:
            for _ in range(tries):
                try:
                    offset, rtt = _query_ntp(server, timeout)
                except Exception as exc:  # noqa: BLE001 - 单个服务器失败不影响其它
                    last_error = f"{server}: {exc}"
                    continue
                if best is None or rtt < best[1]:
                    best = (offset, rtt, server)
                break
            if best is not None and best[1] < 0.05:
                break

        if best is None:
            result = self._sync_http()
            if result.ok:
                return result
            return SyncResult(ok=False, message=last_error or result.message)

        offset, rtt, server = best
        with self._lock:
            self._offset = offset
            self._synced_at = datetime.now(tz=timezone.utc)
            self._source = server
            self._rtt_ms = rtt * 1000
        logger.info("NTP 校时成功 %s 偏移 %.1f ms 往返 %.1f ms", server, offset * 1000, rtt * 1000)
        return SyncResult(True, offset * 1000, server, rtt * 1000, "NTP 校时成功")

    def _sync_http(self, sources: tuple[str, ...] = DEFAULT_HTTP_SOURCES) -> SyncResult:
        last_error = ""
        for url in sources:
            try:
                offset, rtt = _query_http(url)
            except Exception as exc:  # noqa: BLE001
                last_error = f"{url}: {exc}"
                continue
            with self._lock:
                self._offset = offset
                self._synced_at = datetime.now(tz=timezone.utc)
                self._source = url
                self._rtt_ms = rtt * 1000
            logger.info("HTTP 校时成功 %s 偏移 %.1f ms", url, offset * 1000)
            return SyncResult(True, offset * 1000, url, rtt * 1000, "HTTP 校时成功（NTP 不可用）")
        return SyncResult(ok=False, message=f"NTP 与 HTTP 均不可用（{last_error}）")

    def now(self, tz: timezone | None = None) -> datetime:
        """返回校正后的当前时间。"""
        with self._lock:
            offset = self._offset
        corrected = datetime.now(tz=timezone.utc).timestamp() + offset
        return datetime.fromtimestamp(corrected, tz=tz or timezone.utc)

    def seconds_until(self, target: datetime) -> float:
        """距离目标时刻还有多少秒，已过则为负。"""
        return (target - self.now()).total_seconds()
