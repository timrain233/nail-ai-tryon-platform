import csv
import os
import threading
import uuid
import re
from datetime import datetime
from typing import Optional, Any


NAIL_DATABASE_DIR = os.path.dirname(os.path.abspath(__file__))

NAIL_PRODUCT_CSV = os.path.join(NAIL_DATABASE_DIR, "nail_product2.csv")
TEMP_USER_CSV = os.path.join(NAIL_DATABASE_DIR, "temp_user.csv")
USER_BEHAVIOR_LOG_CSV = os.path.join(NAIL_DATABASE_DIR, "user_behavior_log.csv")
HEAT_OPERATION_REPORT_CSV = os.path.join(NAIL_DATABASE_DIR, "heat_operation_report.csv")


# =============================================================
# 线程安全的 CSV 基类
# =============================================================

class CsvTable:
    _locks: dict = {}

    @classmethod
    def _lock(cls, path: str) -> threading.Lock:
        if path not in cls._locks:
            cls._locks[path] = threading.Lock()
        return cls._locks[path]

    def __init__(self, file_path: str, fieldnames: list, primary_key: str = None, auto_init: bool = True):
        self.file_path = file_path
        self.fieldnames = fieldnames
        self.primary_key = primary_key
        if auto_init:
            self._ensure_file()

    def _ensure_file(self):
        if not os.path.exists(self.file_path):
            with open(self.file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()

    def read_all(self) -> list[dict]:
        with self._lock(self.file_path):
            if not os.path.exists(self.file_path):
                return []
            with open(self.file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return [row for row in reader]

    def read_by_column(self, column: str, value: Any) -> list[dict]:
        return [row for row in self.read_all() if row.get(column) == str(value)]

    def read_by_id(self, pk_value: Any) -> Optional[dict]:
        if self.primary_key is None:
            return None
        rows = self.read_by_column(self.primary_key, str(pk_value))
        return rows[0] if rows else None

    def append_row(self, row: dict) -> bool:
        with self._lock(self.file_path):
            try:
                with open(self.file_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                    writer.writerow(row)
                return True
            except Exception:
                return False

    def append_rows(self, rows: list[dict]) -> bool:
        with self._lock(self.file_path):
            try:
                with open(self.file_path, "a", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                    for row in rows:
                        writer.writerow(row)
                return True
            except Exception:
                return False

    def update_row(self, pk_value: Any, updates: dict) -> bool:
        with self._lock(self.file_path):
            try:
                rows = self.read_all()
                updated = False
                for row in rows:
                    if row.get(self.primary_key) == str(pk_value):
                        row.update(updates)
                        updated = True
                if not updated:
                    return False
                with open(self.file_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                    writer.writeheader()
                    writer.writerows(rows)
                return True
            except Exception:
                return False

    def delete_row(self, pk_value: Any) -> bool:
        with self._lock(self.file_path):
            try:
                rows = self.read_all()
                new_rows = [r for r in rows if r.get(self.primary_key) != str(pk_value)]
                if len(new_rows) == len(rows):
                    return False
                with open(self.file_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                    writer.writeheader()
                    writer.writerows(new_rows)
                return True
            except Exception:
                return False

    def count(self) -> int:
        return len(self.read_all())

    def next_id(self) -> int:
        rows = self.read_all()
        if not rows:
            return 1
        ids = []
        for r in rows:
            v = r.get(self.primary_key, "0")
            try:
                ids.append(int(v))
            except ValueError:
                continue
        return max(ids) + 1 if ids else 1

    def overwrite_all(self, rows: list[dict]):
        with self._lock(self.file_path):
            with open(self.file_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=self.fieldnames)
                writer.writeheader()
                writer.writerows(rows)


# =============================================================
# 1. 商品库表
# =============================================================

NAIL_PRODUCT_FIELDS = [
    "item_id", "item_name", "style_label", "color_label",
    "length_label", "nail_shape_label", "scene_label", "skin_label",
    "nail_modify_label", "hand_label", "price_tier", "raw_img_path"
]


class NailProductTable(CsvTable):
    def __init__(self):
        super().__init__(NAIL_PRODUCT_CSV, NAIL_PRODUCT_FIELDS, primary_key="item_id", auto_init=False)

    def get_item_name(self, item_id: int) -> str:
        row = self.read_by_id(item_id)
        return row["item_name"] if row else ""

    def get_item_tags(self, item_id: int) -> dict:
        row = self.read_by_id(item_id)
        if not row:
            return {}
        return {
            "style_label": row.get("style_label", ""),
            "color_label": row.get("color_label", ""),
            "length_label": row.get("length_label", ""),
            "nail_shape_label": row.get("nail_shape_label", ""),
            "scene_label": row.get("scene_label", ""),
            "skin_label": row.get("skin_label", ""),
            "nail_modify_label": row.get("nail_modify_label", ""),
            "hand_label": row.get("hand_label", ""),
            "price_tier": row.get("price_tier", ""),
        }

    def get_all_tags_flat(self, item_id: int) -> str:
        tags = self.get_item_tags(item_id)
        all_values = []
        for v in tags.values():
            for t in v.split(","):
                t = t.strip()
                if t:
                    all_values.append(t)
        return ",".join(all_values)

    def validate_and_standardize(self) -> list[dict]:
        rows = self.read_all()
        standardized = []
        seen_ids = set()
        for row in rows:
            item_id = row.get("item_id", "").strip()
            if not item_id.isdigit():
                continue
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            standardized.append({
                "item_id": item_id,
                "item_name": row.get("item_name", "").strip(),
                "style_label": row.get("style_label", "").replace("，", ",").replace(" ", ""),
                "color_label": row.get("color_label", "").replace("，", ",").replace(" ", ""),
                "length_label": row.get("length_label", "").strip(),
                "nail_shape_label": row.get("nail_shape_label", "").strip(),
                "scene_label": row.get("scene_label", "").replace("，", ",").replace(" ", ""),
                "skin_label": row.get("skin_label", "").replace("，", ",").replace(" ", ""),
                "nail_modify_label": row.get("nail_modify_label", "").replace("，", ",").replace(" ", ""),
                "hand_label": row.get("hand_label", "").replace("，", ",").replace(" ", ""),
                "price_tier": row.get("price_tier", "").strip(),
                "raw_img_path": row.get("raw_img_path", "").strip(),
            })
        self.overwrite_all(standardized)
        return standardized


# =============================================================
# 2. 临时用户信息表
# =============================================================

TEMP_USER_FIELDS = [
    "temp_user_id", "create_time", "device_id", "batch_id", "user_tags"
]


class TempUserTable(CsvTable):
    def __init__(self):
        super().__init__(TEMP_USER_CSV, TEMP_USER_FIELDS, primary_key="temp_user_id")

    def generate_user_id(self, batch_id: str) -> str:
        short_uuid = uuid.uuid4().hex[:8]
        return f"{batch_id}-{short_uuid}"

    def get_or_create_user(self, device_id: str) -> dict:
        existing = self.read_by_column("device_id", device_id)
        if existing:
            return existing[0]
        now = datetime.now()
        batch_id = now.strftime("%Y%m%d")
        temp_user_id = self.generate_user_id(batch_id)
        create_time = now.strftime("%Y-%m-%d %H:%M:%S")
        row = {
            "temp_user_id": temp_user_id,
            "create_time": create_time,
            "device_id": device_id,
            "batch_id": batch_id,
            "user_tags": "",
        }
        self.append_row(row)
        return row

    def append_user_tag(self, temp_user_id: str, new_tag: str):
        row = self.read_by_id(temp_user_id)
        if not row:
            return
        existing_tags = row.get("user_tags", "")
        tags_list = [t.strip() for t in existing_tags.split(",") if t.strip()]
        if new_tag not in tags_list:
            tags_list.append(new_tag)
            self.update_row(temp_user_id, {"user_tags": ",".join(tags_list)})


# =============================================================
# 3. 用户行为日志表
# =============================================================

USER_BEHAVIOR_LOG_FIELDS = [
    "log_id", "temp_user_id", "operate_time", "operate_type",
    "item_id", "item_tags", "stay_duration", "operate_terminal",
    "filter_condition"
]

OPERATE_TYPES = [
    "进入页面", "离开页面", "点击款式", "开始试戴", "结束试戴",
    "点击收藏", "取消收藏", "保存图片", "切换筛选"
]

OPERATE_TERMINALS = ["PC", "移动端", "平板"]


class UserBehaviorLogTable(CsvTable):
    def __init__(self):
        super().__init__(USER_BEHAVIOR_LOG_CSV, USER_BEHAVIOR_LOG_FIELDS, primary_key="log_id")

    def add_log(self, temp_user_id: str, operate_type: str,
                item_id: int = 0, item_tags: str = "",
                stay_duration: float = 0.0, operate_terminal: str = "PC",
                filter_condition: str = "") -> bool:
        if operate_type not in OPERATE_TYPES:
            return False
        if operate_terminal not in OPERATE_TERMINALS:
            operate_terminal = "PC"
        log_id = self.next_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = {
            "log_id": str(log_id),
            "temp_user_id": temp_user_id,
            "operate_time": now,
            "operate_type": operate_type,
            "item_id": str(item_id) if item_id > 0 else "",
            "item_tags": item_tags,
            "stay_duration": f"{stay_duration:.1f}",
            "operate_terminal": operate_terminal,
            "filter_condition": filter_condition,
        }
        return self.append_row(row)

    def get_user_logs(self, temp_user_id: str) -> list[dict]:
        return self.read_by_column("temp_user_id", temp_user_id)

    def get_item_logs(self, item_id: int) -> list[dict]:
        return self.read_by_column("item_id", str(item_id))

    def count_by_operate_type(self, item_id: int, operate_type: str) -> int:
        logs = self.get_item_logs(item_id)
        return sum(1 for log in logs if log.get("operate_type") == operate_type)

    def stat_item_click(self, item_id: int) -> int:
        return self.count_by_operate_type(item_id, "点击款式")

    def stat_item_try_on(self, item_id: int) -> int:
        return self.count_by_operate_type(item_id, "开始试戴")

    def stat_item_favorite(self, item_id: int) -> int:
        return self.count_by_operate_type(item_id, "点击收藏")


# =============================================================
# 4. AI商业运营报表
# =============================================================

HEAT_OPERATION_REPORT_FIELDS = [
    "report_id", "statistics_time", "statistics_cycle", "item_id",
    "item_name", "item_tags", "total_click", "total_try_on",
    "try_on_conversion_rate", "total_favorite", "favorite_conversion_rate",
    "heat_level", "recommend_operation_strategy", "inventory_suggestion",
    "tag_heat_analysis"
]

HEAT_LEVELS = ["S级爆款", "A级潜力款", "B级常规款", "C级冷门款"]
STATISTICS_CYCLES = ["实时", "日度", "周度", "批次"]


class HeatOperationReportTable(CsvTable):
    def __init__(self):
        super().__init__(HEAT_OPERATION_REPORT_CSV, HEAT_OPERATION_REPORT_FIELDS,
                         primary_key="report_id")

    def _calc_heat_level(self, click: int, try_on: int, favorite: int,
                         try_rate: float, fav_rate: float) -> str:
        score = 0
        if click >= 50:
            score += 3
        elif click >= 20:
            score += 2
        elif click >= 5:
            score += 1
        if try_on >= 20:
            score += 3
        elif try_on >= 8:
            score += 2
        elif try_on >= 3:
            score += 1
        if favorite >= 10:
            score += 3
        elif favorite >= 4:
            score += 2
        elif favorite >= 1:
            score += 1
        if try_rate >= 50:
            score += 2
        elif try_rate >= 30:
            score += 1
        if fav_rate >= 30:
            score += 2
        elif fav_rate >= 15:
            score += 1
        if score >= 10:
            return "S级爆款"
        elif score >= 6:
            return "A级潜力款"
        elif score >= 3:
            return "B级常规款"
        else:
            return "C级冷门款"

    def _gen_strategy(self, level: str, item_name: str,
                      click: int, try_on: int, favorite: int) -> str:
        strategies = {
            "S级爆款": f"【强力主推】{item_name}数据表现优异，建议增加首页曝光位，搭配关联款组合推荐，"
                      f"可尝试同风格系列化开发，优先补充库存",
            "A级潜力款": f"【重点培育】{item_name}转化率良好，建议优化展示顺序，增加场景化穿搭推荐，"
                       f"结合热门标签做精准推送提升曝光",
            "B级常规款": f"【常规运营】{item_name}表现稳定，建议结合节日/季节主题做限时活动，"
                       f"搭配高热度款组合推荐，关注标签优化空间",
            "C级冷门款": f"【观察调整】{item_name}数据偏低，建议检查款式标签准确性，优化缩略图质量，"
                       f"尝试调整价格策略或捆绑热门款推荐",
        }
        return strategies.get(level, "")

    def _gen_inventory(self, level: str) -> str:
        suggestions = {
            "S级爆款": "高库存备货，建议保持15-30天安全库存，优先补货通道",
            "A级潜力款": "中等库存备货，建议保持7-15天安全库存，关注转化趋势及时补货",
            "B级常规款": "标准库存备货，建议保持3-7天安全库存，按需补货",
            "C级冷门款": "低库存维持，建议保持1-3天安全库存或按单生产，避免积压",
        }
        return suggestions.get(level, "")

    def _gen_tag_analysis(self, item_id: int, item_tags: str,
                          click: int, try_on: int) -> str:
        tags = [t.strip() for t in item_tags.split(",") if t.strip()]
        if not tags:
            return "暂无标签数据，建议完善款式标签以便精准分析"
        top_tags = tags[:3]
        if click > 0:
            return (f"核心标签 '{', '.join(top_tags)}' 表现突出，"
                    f"该类标签款式整体点击{click}次、试戴{try_on}次，"
                    f"建议围绕这些标签做内容营销和场景化推荐")
        return f"标签 '{', '.join(top_tags)}' 暂未产生足够数据，建议优化标签准确性并提升曝光"

    def generate_report(self, item_id: int, product: dict,
                        log_table: UserBehaviorLogTable,
                        cycle: str = "实时") -> dict:
        if cycle not in STATISTICS_CYCLES:
            cycle = "实时"
        click = log_table.stat_item_click(item_id)
        try_on = log_table.stat_item_try_on(item_id)
        favorite = log_table.stat_item_favorite(item_id)
        try_rate = (try_on / click * 100) if click > 0 else 0.0
        fav_rate = (favorite / click * 100) if click > 0 else 0.0
        item_tags = product.get("style_label", "")
        heat_level = self._calc_heat_level(click, try_on, favorite, try_rate, fav_rate)
        report_id = self.next_id()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        row = {
            "report_id": str(report_id),
            "statistics_time": now,
            "statistics_cycle": cycle,
            "item_id": str(item_id),
            "item_name": product.get("item_name", ""),
            "item_tags": item_tags,
            "total_click": str(click),
            "total_try_on": str(try_on),
            "try_on_conversion_rate": f"{try_rate:.2f}%",
            "total_favorite": str(favorite),
            "favorite_conversion_rate": f"{fav_rate:.2f}%",
            "heat_level": heat_level,
            "recommend_operation_strategy": self._gen_strategy(
                heat_level, product.get("item_name", ""), click, try_on, favorite),
            "inventory_suggestion": self._gen_inventory(heat_level),
            "tag_heat_analysis": self._gen_tag_analysis(item_id, item_tags, click, try_on),
        }
        return row

    def generate_all_reports(self, product_table: NailProductTable,
                             log_table: UserBehaviorLogTable,
                             cycle: str = "实时") -> list[dict]:
        products = product_table.read_all()
        rows = []
        for prod in products:
            item_id = int(prod["item_id"])
            row = self.generate_report(item_id, prod, log_table, cycle)
            rows.append(row)
        if rows:
            self.append_rows(rows)
        return rows


# =============================================================
# 统一的 DataManager 入口
# =============================================================

class DataManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def initialize(self):
        if self._initialized:
            return
        self.product = NailProductTable()
        self.user = TempUserTable()
        self.log = UserBehaviorLogTable()
        self.report = HeatOperationReportTable()
        self.product.validate_and_standardize()
        self._initialized = True

    def get_product_tags_flat(self, item_id: int) -> str:
        return self.product.get_all_tags_flat(item_id)

    def get_or_create_user(self, device_id: str) -> dict:
        return self.user.get_or_create_user(device_id)

    def add_behavior_log(self, temp_user_id: str, operate_type: str,
                         item_id: int = 0, stay_duration: float = 0.0,
                         operate_terminal: str = "PC",
                         filter_condition: str = "") -> bool:
        item_tags = ""
        if item_id > 0:
            item_tags = self.product.get_all_tags_flat(item_id)
        return self.log.add_log(
            temp_user_id=temp_user_id,
            operate_type=operate_type,
            item_id=item_id,
            item_tags=item_tags,
            stay_duration=stay_duration,
            operate_terminal=operate_terminal,
            filter_condition=filter_condition,
        )

    def generate_reports(self, cycle: str = "实时") -> list[dict]:
        return self.report.generate_all_reports(self.product, self.log, cycle)


dm = DataManager()