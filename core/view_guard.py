"""
ViewGuard — بدون سهمیه روزانه، بدون فاصله اجباری
فقط flood cooldown و جلوگیری از ویو تکراری روی یک پیام نگه داشته شده.
"""

import time as _time
import random
import asyncio
from collections import defaultdict

# ───────── تنظیمات ─────────
MAX_VIEWS_PER_DAY   = 9999   # عملاً بی‌نهایت — محدودیت روزانه برداشته شده
MIN_GAP_SEC         = 0.0    # بدون فاصله اجباری بین ویوها
MAX_GAP_SEC         = 10.0
FLOOD_COOLDOWN_MIN  = 60     # cooldown بعد از flood — حداقل ۱ دقیقه
FLOOD_COOLDOWN_MAX  = 180    # حداکثر ۳ دقیقه
SEEN_MSG_TTL        = 86400  # چقدر "دیده‌شده" نگه داریم (۲۴ ساعت)


class _AccountState:
    __slots__ = ("last_view_at", "views_today", "day_key",
                 "flood_until", "seen_msgs", "seen_times")

    def __init__(self):
        self.last_view_at: float = 0.0
        self.views_today:  int   = 0
        self.day_key:      str   = ""
        self.flood_until:  float = 0.0
        self.seen_msgs:    set   = set()
        self.seen_times:   dict  = {}


class ViewGuard:
    def __init__(self):
        self._state: dict[str, _AccountState] = defaultdict(_AccountState)
        self._lock = asyncio.Lock()

    def _today(self) -> str:
        from datetime import date
        return date.today().isoformat()

    def _st(self, phone: str) -> _AccountState:
        return self._state[phone]

    def _reset_day_if_needed(self, st: _AccountState):
        today = self._today()
        if st.day_key != today:
            st.day_key     = today
            st.views_today = 0
            now = _time.time()
            old = [mid for mid, ts in st.seen_times.items() if now - ts > SEEN_MSG_TTL]
            for mid in old:
                st.seen_msgs.discard(mid)
                del st.seen_times[mid]

    def can_view(self, phone: str, msg_id: int) -> tuple[bool, float]:
        st = self._st(phone)
        self._reset_day_if_needed(st)
        now = _time.time()

        # flood فعاله؟
        if st.flood_until > now:
            return False, st.flood_until - now

        # این پست رو قبلاً ویو زده؟ — این چک نگه داشته میشه (تکرار بی‌معنیه)
        if msg_id in st.seen_msgs:
            return False, -1

        return True, 0.0

    def on_view_ok(self, phone: str, msg_id: int):
        st = self._st(phone)
        self._reset_day_if_needed(st)
        now = _time.time()
        st.last_view_at = now
        st.views_today  += 1
        st.seen_msgs.add(msg_id)
        st.seen_times[msg_id] = now
        total = sum(s.views_today for s in self._state.values())
        if total % 10 == 0 or st.views_today == 1:
            try:
                from models import database as _db
                _db.set_setting("vg_total_views_today", total)
                _db.set_setting("vg_counter_date", self._today())
            except Exception:
                pass

    def on_flood(self, phone: str, seconds: int):
        st = self._st(phone)
        cooldown = max(seconds, random.randint(FLOOD_COOLDOWN_MIN, FLOOD_COOLDOWN_MAX))
        st.flood_until = _time.time() + cooldown

    def eligible_phones(self, phones: list[str], msg_id: int) -> list[str]:
        result = []
        for p in phones:
            ok, wait = self.can_view(p, msg_id)
            if ok:
                result.append(p)
        return result

    def stats(self) -> dict:
        now = _time.time()
        flooded   = sum(1 for st in self._state.values() if st.flood_until > now)
        exhausted = sum(1 for st in self._state.values() if st.views_today >= MAX_VIEWS_PER_DAY)
        mem_views = sum(st.views_today for st in self._state.values())

        if mem_views == 0:
            try:
                from models import database as _db
                saved_date = _db.get_setting("vg_counter_date", "")
                if saved_date == self._today():
                    mem_views = int(_db.get_setting("vg_total_views_today", 0))
            except Exception:
                pass

        return {
            "total_accounts":    len(self._state),
            "flooded_now":       flooded,
            "exhausted_today":   exhausted,
            "total_views_today": mem_views,
        }


# singleton
VIEW_GUARD = ViewGuard()


def _load_settings_from_db():
    global MAX_VIEWS_PER_DAY, MIN_GAP_SEC, MAX_GAP_SEC
    try:
        from core.db import db
        v = db.get_setting("vg_max_views_per_day", None)
        if v is not None:
            MAX_VIEWS_PER_DAY = int(v)
        mn = db.get_setting("vg_min_gap", None)
        if mn is not None:
            MIN_GAP_SEC = float(mn)
        mx = db.get_setting("vg_max_gap", None)
        if mx is not None:
            MAX_GAP_SEC = float(mx)
    except Exception:
        pass


try:
    _load_settings_from_db()
except Exception:
    pass
