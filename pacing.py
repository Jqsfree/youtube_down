"""批量下载自适应间隔 —— 纯逻辑，不依赖 PySide6 / yt-dlp。"""

from __future__ import annotations

import random
import time


class AdaptivePacer:
    """自适应批量间隔：连续成功略提速，bot/429 退避。"""

    def __init__(
        self,
        sleep_min: float,
        sleep_max: float,
        *,
        ok_streak_to_speedup: int = 15,
        speedup_step: float = 0.5,
        min_floor: float = 1.5,
        max_ceiling: float = 30.0,
        bot_pause_sec: float = 120.0,
    ) -> None:
        self.sleep_min = sleep_min
        self.sleep_max = sleep_max
        self.ok_streak = 0
        self.ok_streak_to_speedup = ok_streak_to_speedup
        self.speedup_step = speedup_step
        self.min_floor = min_floor
        self.max_ceiling = max_ceiling
        self.bot_pause_sec = bot_pause_sec

    def wait(self) -> None:
        if self.sleep_max <= 0:
            return
        lo = max(0.0, self.sleep_min)
        hi = max(lo, self.sleep_max)
        time.sleep(random.uniform(lo, hi))

    def on_success(self) -> None:
        self.ok_streak += 1
        if self.ok_streak >= self.ok_streak_to_speedup:
            self.sleep_min = max(self.min_floor, self.sleep_min - self.speedup_step)
            self.sleep_max = max(self.sleep_min, self.sleep_max - self.speedup_step)
            self.ok_streak = 0

    def on_skip(self) -> None:
        self.ok_streak += 1

    def on_bot_or_rate_limit(self) -> None:
        self.ok_streak = 0
        self.sleep_min = min(self.max_ceiling, max(self.min_floor, self.sleep_min * 2))
        self.sleep_max = min(self.max_ceiling, max(self.sleep_min, self.sleep_max * 2))
        print(
            f"触发限流/bot，退避 {self.bot_pause_sec:.0f}s，"
            f"间隔调整为 {self.sleep_min:.1f}-{self.sleep_max:.1f}s",
            flush=True,
        )
        time.sleep(self.bot_pause_sec)
