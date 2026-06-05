"""
Simai 谱面渲染引擎
将解析后的音符数据渲染为可视化图片


轨道方向: 从左到右 8 7 6 5 4 3 2 1
"""

import math
import colorsys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ===================== 固定参数 =====================
CANVAS_HEIGHT = 3072

# 分音计算常量（与 init.py 对齐）
FRAC_BASE = 768
FRAC_TOLERANCE = 4
MARGIN = 200
VERTICAL_SECTION_COUNT_DEFAULT = 8  # 默认每个 Unit 显示的小节数

TRACK_WIDTH = 36           # 单轨宽度 (容纳 30x30 贴图)
TRACK_PADDING = 6          # 轨间距
UNIT_PADDING_RATIO = 5/8

# BPM 分界线（低BPM拉伸判定）
BPM_THRESHOLD = 110


NOTE_SIZE = 30             # 贴图显示尺寸

# ===================== 配色系统 =====================
BG_COLOR = (18, 18, 24)
COLUMN_BG_COLOR = (28, 28, 36)
LINE_MAIN = (90, 90, 110)
LINE_SUB = (45, 45, 55)

TAP_COLOR = (105, 120, 168)
HOLD_COLOR = (191, 165, 215)

BPM_COLOR = (255, 210, 0)
BAR_NUM_COLOR = (100, 105, 130)
TIME_COLOR = (80, 85, 110)
STAT_LABEL_COLOR = (100, 105, 130)
STAT_VALUE_COLOR = (200, 205, 220)

SLIDE_LINE_COLOR = (255, 200, 100)
SLIDE_TAME_DASH_COLOR = (200, 200, 220, 180)

SRC_DIR = Path(__file__).parent / "src"


class TextureCache:
    """贴图缓存，按需加载和缩放"""
    def __init__(self):
        self._cache = {}
    
    def get(self, name, size=None):
        """获取贴图，可选指定尺寸"""
        key = (name, size)
        if key not in self._cache:
            path = SRC_DIR / name
            if not path.exists():
                return None
            img = Image.open(path).convert("RGBA")
            if size:
                img = img.resize(size, Image.Resampling.LANCZOS)
            self._cache[key] = img
        return self._cache[key]


class ChartCanvas:
    """谱面画布"""
    
    def __init__(self, notes, bpm_events, first_offset=0.0, song_info=None, bg_img_path=None):
        self.notes = notes
        self.bpm_events = bpm_events
        self.first_offset = first_offset
        self.song_info = song_info or {}
        self.bg_img_path = bg_img_path
        
        # 根据 BPM 分布决定每 Unit 的小节数（低BPM拉伸）
        self.vsc = self._determine_vsc()
        
        # 计算最大时间范围
        self.max_bar = self._get_max_bar()
        self.total_units = max(1, int(math.ceil(self.max_bar / self.vsc)))
        
        # 布局参数
        self.unit_w = 8 * TRACK_WIDTH + 7 * TRACK_PADDING  # = 330
        self.unit_p = int(self.unit_w * UNIT_PADDING_RATIO)
        
        # 计算画布总宽（右侧留白 MARGIN 即可）
        self.canvas_w = int(MARGIN + self.total_units * (self.unit_w + self.unit_p) + MARGIN)
        
        # 创建图片
        self.img = Image.new("RGB", (self.canvas_w, CANVAS_HEIGHT), BG_COLOR)
        self.draw = ImageDraw.Draw(self.img)
        
        # 贴图缓存
        self.textures = TextureCache()
        
        # 布局计算
        # section_h = 每个小节的高度（可用高度 / 每栏小节数）
        self.section_h = (CANVAS_HEIGHT - 2 * MARGIN) / self.vsc
        self.subdiv_h = self.section_h / 16  # 每拍子线
        
        # 已渲染的touch/touchhold位置（避免重复绘制）
        self._touch_rendered = set()
    
    def _determine_vsc(self):
        """根据 BPM 分布决定每 Unit 的小节数
        
        遍历所有 BPM 事件，统计 <BPM_THRESHOLD 和 >=BPM_THRESHOLD 的数量。
        低BPM多 → 拉伸（vsc=4），高BPM多 → 保留（vsc=8）
        """
        if not self.bpm_events:
            return VERTICAL_SECTION_COUNT_DEFAULT
        
        low_count = sum(1 for e in self.bpm_events if e['bpm'] < BPM_THRESHOLD)
        high_count = sum(1 for e in self.bpm_events if e['bpm'] >= BPM_THRESHOLD)
        
        return 4 if low_count >= high_count else 8
    
    def _get_max_bar(self):
        """获取最大时间（bar 为单位，不追加空栏）"""
        max_b = 0.0
        for n in self.notes:
            if n.beat_pos > max_b:
                max_b = n.beat_pos
            if n.end_beat_pos > max_b:
                max_b = n.end_beat_pos
        return max_b
    
    def get_unit_x_range(self, unit_idx):
        """获取 Unit 的 x 范围"""
        start_x = MARGIN + unit_idx * (self.unit_w + self.unit_p)
        return start_x, start_x + self.unit_w
    
    def bar_to_pixel(self, bar_pos, column):
        """将时间位置和轨道转换为像素坐标"""
        unit_idx = int(bar_pos // self.vsc)
        inner_bar = bar_pos % self.vsc
        unit_start_x, _ = self.get_unit_x_range(unit_idx)
        x = unit_start_x + column * (TRACK_WIDTH + TRACK_PADDING) + 3
        # section_h = 每个小节的高度, inner_bar = 第几小节(0~1)
        y = CANVAS_HEIGHT - MARGIN - inner_bar * self.section_h
        
        # 硬编码修复：跑到顶部边框之上（y < 201）时强制移到下一个unit底部
        if y < MARGIN + 1:
            unit_idx += 1
            unit_start_x, _ = self.get_unit_x_range(unit_idx)
            x = unit_start_x + column * (TRACK_WIDTH + TRACK_PADDING) + 3
            y = CANVAS_HEIGHT - MARGIN
        
        return x, y, unit_idx
    
    def bar_to_pixel_y(self, bar_pos):
        """仅将时间位置转换为 y 坐标（跨Unit）
        
        ⚠️ 注意：bar_pos % self.vsc 在整除时会回绕。
        跨Unit分割的segment应使用 _bar_to_pixel_y_in_unit。
        """
        unit_idx = int(bar_pos // self.vsc)
        inner_bar = bar_pos % self.vsc
        return CANVAS_HEIGHT - MARGIN - inner_bar * self.section_h
    
    def _bar_to_pixel_y_in_unit(self, bar_pos, unit_idx):
        """在指定Unit内将 bar_pos 转换为 y 坐标（线性，不回绕）
        
        当 segment 跨越 unit 边界时，seg_end 可能等于 unit_idx * self.vsc，
        此时 bar_pos % self.vsc = 0 会导致 bar_to_pixel_y 错误返回底部。
        本函数相对于指定unit的起始bar计算线性偏移，避免回绕。
        
        公式: y = CANVAS_HEIGHT - MARGIN - (bar_pos - unit_idx*VSC) * section_h
        unit底部(bar=unit_idx*VSC) → y=2872, unit顶部(bar=(unit_idx+1)*VSC) → y=200
        """
        inner = bar_pos - unit_idx * self.vsc
        return CANVAS_HEIGHT - MARGIN - inner * self.section_h
    
    def _get_unit_segments(self, beat_start, beat_end, column):
        """获取 [beat_start, beat_end] 经过的所有 Unit 的分段
        
        对于跨多Unit的连续元素(hold/touchhold/slide)，
        每个Unit渲染自己的那一段，使用该Unit的x坐标。
        
        Returns: [(unit_idx, local_start_beat, local_end_beat, x), ...]
        """
        start_unit = int(beat_start // self.vsc)
        end_unit = int(beat_end // self.vsc)
        
        segments = []
        for unit_idx in range(start_unit, end_unit + 1):
            unit_bar_start = unit_idx * self.vsc
            unit_bar_end = (unit_idx + 1) * self.vsc
            
            seg_start = max(beat_start, unit_bar_start)
            seg_end = min(beat_end, unit_bar_end)
            
            if seg_start >= seg_end:
                continue
            
            unit_start_x, _ = self.get_unit_x_range(unit_idx)
            x = unit_start_x + column * (TRACK_WIDTH + TRACK_PADDING) + 3
            
            segments.append((unit_idx, seg_start, seg_end, x))
        
        return segments
    
    def render(self):
        """完整渲染流程"""
        self._draw_song_header()
        self._draw_grid()
        self._draw_info()
        self._draw_subdivision_annotations()
        
        # 绘制烟花特效（在网格上方，hold/slide线/贴图下方）
        for note in self.notes:
            if note.firework:
                self._draw_firework_effect(note)
        
        # 绘制所有hold条和slide线
        for note in self.notes:
            if note.type == "hold":
                self._draw_hold_bar_only(note)
            elif note.type == "touchhold":
                self._draw_touchhold_bar_only(note)
            elif note.type == "slide":
                self._draw_slide_line_only(note)
        
        # 再绘制贴图在最上层
        for note in self.notes:
            if note.type == "tap":
                self._draw_tap(note)
            elif note.type == "hold":
                self._draw_hold_head(note)
            elif note.type == "touch":
                self._draw_touch(note)
            elif note.type == "touchhold":
                self._draw_touchhold_head(note)
            elif note.type == "slide":
                self._draw_slide_head(note)
        
        return self.img
    
    def _draw_grid(self):
        """绘制网格线"""
        grid_right_ext = self.unit_w + 8
        
        # 每个 Unit 的独立底板背景（从顶部 MARGIN 到底部画布边缘）
        # 只填充 Unit 自身宽度范围，unit_p 间隙区域不填充
        for u in range(self.total_units):
            x1, _ = self.get_unit_x_range(u)
            self.draw.rectangle([(x1, MARGIN), (x1 + self.unit_w + 8, CANVAS_HEIGHT - 200)], fill=COLUMN_BG_COLOR)
        
        # 横向主格线 (小节线)
        for i in range(self.vsc + 1):
            y = CANVAS_HEIGHT - MARGIN - i * self.section_h
            for u in range(self.total_units):
                x1, _ = self.get_unit_x_range(u)
                self.draw.line([(x1, y), (x1 + grid_right_ext, y)], fill=LINE_MAIN, width=1)
        
        # 横向次格线 (拍子线)
        for u in range(self.total_units):
            x1, _ = self.get_unit_x_range(u)
            for i in range(self.vsc):
                base = CANVAS_HEIGHT - MARGIN - i * self.section_h
                for sub in range(1, 16):
                    self.draw.line([(x1, base - sub * self.subdiv_h), (x1 + grid_right_ext, base - sub * self.subdiv_h)], fill=LINE_SUB, width=1)
        
        # 纵向轨道线 (8 条)
        for u in range(self.total_units):
            x, _ = self.get_unit_x_range(u)
            for c in range(9):
                cx = x + c * (TRACK_WIDTH + TRACK_PADDING)
                self.draw.line([(cx, MARGIN), (cx, CANVAS_HEIGHT - MARGIN)], fill=LINE_MAIN, width=2)
        
        # 轨道编号标注 (8~1 从左到右)
        try:
            font = ImageFont.load_default(size=16)
        except:
            font = ImageFont.load_default()
        for u in range(self.total_units):
            x, _ = self.get_unit_x_range(u)
            for c in range(8):
                btn_num = 8 - c
                cx = x + c * (TRACK_WIDTH + TRACK_PADDING) + TRACK_WIDTH // 2
                self.draw.text((cx - 6, CANVAS_HEIGHT - MARGIN + 8), str(btn_num), fill=BPM_COLOR, font=font)
    
    def _calculate_bar_time(self, bar_pos):
        """按 BPM 事件分段精确计算 bar_pos 处的时间（秒）
        
        从开头累加，每个 BPM 段使用自己的 BPM 值计算。
        first_offset 用于校准第一个音符的延迟（&first）。
        """
        if not self.bpm_events:
            total = bar_pos * 4 * (60.0 / 120.0)
            return total + self.first_offset
        
        total_sec = 0.0
        prev_beat = 0.0
        prev_bpm = self.bpm_events[0]['bpm']
        
        for event in sorted(self.bpm_events, key=lambda e: e['beat']):
            if event['beat'] > bar_pos:
                total_sec += (bar_pos - prev_beat) * 4 * (60.0 / prev_bpm)
                return total_sec + self.first_offset
            total_sec += (event['beat'] - prev_beat) * 4 * (60.0 / prev_bpm)
            prev_beat = event['beat']
            prev_bpm = event['bpm']
        
        total_sec += (bar_pos - prev_beat) * 4 * (60.0 / prev_bpm)
        return total_sec + self.first_offset
    
    def _draw_song_header(self):
        """绘制顶部歌曲信息（标题、艺术家、设计者、难度、背景图）"""
        try:
            font_title = ImageFont.load_default(size=48)
            font_info = ImageFont.load_default(size=32)
        except:
            font_title = font_info = ImageFont.load_default()
        
        title = self.song_info.get("title", "") if self.song_info else ""
        artist = self.song_info.get("artist", "") if self.song_info else ""
        designer = self.song_info.get("designer", "") if self.song_info else ""
        diff_name = self.song_info.get("diff_name", "") if self.song_info else ""
        
        # 背景图（左上角 275x100 banner），无指定图则用 unknown.jpg
        bg_img = self.bg_img_path
        if not bg_img:
            unknown_path = Path(__file__).parent / "unknown.jpg"
            if unknown_path.exists():
                bg_img = unknown_path
        
        if bg_img:
            try:
                bg_pil = Image.open(str(bg_img)).convert("RGBA")
                target_w, target_h = 275, 100
                
                bg_w, bg_h = bg_pil.size
                scale = max(target_w / bg_w, target_h / bg_h)
                new_w = int(bg_w * scale)
                new_h = int(bg_h * scale)
                
                bg_resized = bg_pil.resize((new_w, new_h), Image.Resampling.LANCZOS)
                
                crop_left = (new_w - target_w) // 2
                crop_top = (new_h - target_h) // 2
                crop_right = crop_left + target_w
                crop_bottom = crop_top + target_h
                bg_cropped = bg_resized.crop((crop_left, crop_top, crop_right, crop_bottom))
                
                # 粘贴到左上角
                self.img.paste(bg_cropped, (MARGIN, MARGIN - 150), mask=bg_cropped.split()[-1])
                header_x = MARGIN + target_w + 40
            except Exception as e:
                print(f"⚠️ 背景图加载失败: {e}")
                header_x = MARGIN
        else:
            header_x = MARGIN
        
        # 文字信息（artist - title 顺序）
        if title or artist:
            display = f"{artist} - {title}"
        else:
            display = "Unknown - 临时测试"
        
        self.draw.text((header_x, MARGIN - 160), display, fill=(255, 255, 255), font=font_title)
        
        parts = []
        if designer:
            parts.append(f"Designer: {designer}")
        if diff_name:
            parts.append(f"Diff: {diff_name}")
        if parts:
            self.draw.text((header_x, MARGIN - 100), " | ".join(parts), fill=(140, 145, 160), font=font_info)
    
    def _draw_info(self):
        """绘制辅助信息"""
        try:
            font_bar = ImageFont.load_default(size=20)
            font_time = ImageFont.load_default(size=12)
            font_bpm = ImageFont.load_default(size=14)
            font_stat_label = ImageFont.load_default(size=24)
            font_stat_value = ImageFont.load_default(size=28)
        except:
            font_bar = font_time = font_bpm = font_stat_label = font_stat_value = ImageFont.load_default()
        
        # 判断是否需要在左侧显示时间
        # 有 (bpm) 标记 → 显示；没有 → 隐藏
        show_time = len(self.bpm_events) > 0
        
        # 左侧小节编号和时间（跨Unit偏移）
        for u in range(self.total_units):
            base_bar = u * self.vsc
            x, _ = self.get_unit_x_range(u)
            for i in range(self.vsc):
                bar_pos = base_bar + i        # 0-based bar position
                bar_num = bar_pos + 1          # 显示为1-based
                # i = Unit内第几小节, section_h = 每个小节的高度
                y = CANVAS_HEIGHT - MARGIN - i * self.section_h - 10
                self.draw.text((x - 60, y), f"{bar_num}", fill=BAR_NUM_COLOR, font=font_bar)
                
                # 只有有BPM变更时才显示时间，且只显示小节线位置的时间
                if show_time:
                    total_sec = self._calculate_bar_time(bar_pos)
                    m = int(total_sec // 60)
                    s = int(total_sec % 60)
                    ms = int(round((total_sec - int(total_sec)) * 1000))
                    self.draw.text((x - 82, y + 24), f"{m:02d}:{s:02d}.{ms:03d}", fill=TIME_COLOR, font=font_time)
        
        # BPM 事件标注（在正确的Unit上标注）
        for event in self.bpm_events:
            beat = event['beat']
            bpm_val = event['bpm']
            # 找到该 beat 所在的 Unit
            unit_idx = int(beat // self.vsc)
            if unit_idx >= self.total_units:
                unit_idx = self.total_units - 1
            y = self._bar_to_pixel_y_in_unit(beat, unit_idx)
            x1, _ = self.get_unit_x_range(unit_idx)
            self.draw.text((x1 - 30, y - 10), f"{int(bpm_val)}", fill=BPM_COLOR, font=font_bpm)
        
        # 统计信息
        total_notes = len(self.notes)
        tap_count = sum(1 for n in self.notes if n.type == "tap")
        hold_count = sum(1 for n in self.notes if n.type == "hold")
        # SLIDE = 有star贴图（渲染星星）的slide个数
        slide_star_count = sum(1 for n in self.notes if n.type == "slide" and not n.no_start_star)
        touch_count = sum(1 for n in self.notes if n.type in ("touch", "touchhold"))
        break_count = sum(1 for n in self.notes if n.break_note or n.slide_start_break)
        ex_count = sum(1 for n in self.notes if n.ex)
        
        base_x = 200
        base_y = CANVAS_HEIGHT - 140
        
        self.draw.text((base_x, base_y), "MEASURES:", fill=STAT_LABEL_COLOR, font=font_stat_label)
        self.draw.text((base_x + 130, base_y - 4), f"{int(self.max_bar) + 1}", fill=STAT_VALUE_COLOR, font=font_stat_value)
        
        self.draw.text((base_x + 240, base_y), f"TOTAL:", fill=STAT_LABEL_COLOR, font=font_stat_label)
        self.draw.text((base_x + 340, base_y - 4), f"{total_notes}", fill=STAT_VALUE_COLOR, font=font_stat_value)
        
        self.draw.text((base_x + 240, base_y + 40), f"TAP: {tap_count}", fill=TAP_COLOR, font=font_stat_label)
        self.draw.text((base_x + 380, base_y + 40), f"HOLD: {hold_count}", fill=HOLD_COLOR, font=font_stat_label)
        self.draw.text((base_x + 530, base_y + 40), f"SLIDE: {slide_star_count}", fill=SLIDE_LINE_COLOR, font=font_stat_label)
        self.draw.text((base_x + 680, base_y + 40), f"TOUCH: {touch_count}", fill=BPM_COLOR, font=font_stat_label)
        self.draw.text((base_x + 830, base_y + 40), f"BREAK: {break_count}", fill=(255, 120, 120), font=font_stat_label)
        self.draw.text((base_x + 1000, base_y + 40), f"EX: {ex_count}", fill=(200, 200, 80), font=font_stat_label)
        
        if self.bpm_events:
            first_bpm = self.bpm_events[0]['bpm']
            self.draw.text((base_x, base_y + 80), f"BPM: {int(first_bpm)}", fill=BPM_COLOR, font=font_stat_value)
    
    # ---- 分音标注：相差-映射法（与 init.py 对齐） ----
    
    def _delta_to_criterion(self, delta):
        """将 delta（768单位）映射为分音等级
        768 = 4分音(1拍), 3072 = 1分音(整小节)
        精确匹配不到时取最接近的
        """
        mapping = {
            # 基础分音
            3072: "1",      # {1}  整小节
            1536: "2",      # {2}  半小节
            1024: "3",      # {3}  3分音
            768:  "4",      # {4}  4分音(1拍)
            614:  "5",      # {5}  5分音
            576: "2.",
            512:  "6",      # {6}  6分音
            439:  "7",      # {7}  7分音
            384:  "8",      # {8}  8分音
            341:  "9",      # {9}  9分音
            307:  "10",     # {10} 10分音
            288: "4.",
            256:  "12",     # {12} 12分音
            219:  "14",     # {14} 14分音
            192:  "16",     # {16} 16分音
            171:  "18",     # {18} 18分音
            154:  "20",     # {20} 20分音
            144: "8.",
            128:  "24",     # {24} 24分音
            96:   "32",     # {32} 32分音
            77:   "40",     # {40} 40分音
            64:   "48",     # {48} 48分音
            48:   "64",     # {64} 64分音
            32:   "96",     # {96} 96分音
        }
        if delta in mapping:
            return mapping[delta]
        closest = min(mapping.keys(), key=lambda k: abs(k - delta))
        return mapping[closest]
    
    def _collect_time_points(self):
        """收集所有音符的时间点（start+end）用于分音计算
        注意：slide 的末端（star终点）不参与分音标注。
        """
        points = set()
        for n in self.notes:
            points.add(round(n.beat_pos, 6))
            if n.type in ("hold", "touchhold") and n.end_beat_pos > n.beat_pos:
                points.add(round(n.end_beat_pos, 6))
        sorted_p = sorted(points)
        # 精密度去重（与 init.py 的逻辑一致）
        deduped = []
        for p in sorted_p:
            if not deduped:
                deduped.append(p)
                continue
            last_abs = round(deduped[-1] * 3072)
            curr_abs = round(p * 3072)
            if abs(curr_abs - last_abs) <= FRAC_TOLERANCE:
                continue
            deduped.append(p)
        return deduped
    
    def _compute_subdivision_labels(self):
        """计算分音标注映射表 {beat_pos: label_str}"""
        points = self._collect_time_points()
        if len(points) < 2:
            return {}
        labels = {}
        for i in range(len(points) - 1):
            curr = points[i]
            nxt = points[i + 1]
            delta_bars = nxt - curr
            delta_768 = round(delta_bars * 3072)
            if delta_768 <= 0:
                continue
            labels[curr] = self._delta_to_criterion(delta_768)
        # 最后一个点沿用前一个标注
        if len(points) >= 2:
            labels[points[-1]] = labels.get(points[-2], "")
        return labels
    
    def _draw_subdivision_annotations(self):
        """在侧边栏绘制分音标注（Unit右侧）
        
        双层标注系统：
          第一层（黄色）：相差-映射法计算的分音等级
            所有 note/star/hold头/touchhold头/touch → 黄色
          第二层（灰色）：[] 内的原始数值，直接搬过来
            放在黄色文字的右测（右移），多个用逗号隔开
        
        采用 init.py 的相差-映射法：
        1. 收集所有 time point（各类型 note 的 start + end）
        2. 排序去重 → 相邻求 delta → 查表映射
        3. 每个 time point 渲染对应分音数字
        """
        # ---- 收集 raw_bracket 按精确位置 ----
        raw_brackets_by_pos = {}
        for n in self.notes:
            if n.raw_bracket:
                pos_key = round(n.beat_pos, 6)
                if pos_key not in raw_brackets_by_pos:
                    raw_brackets_by_pos[pos_key] = []
                if n.raw_bracket not in raw_brackets_by_pos[pos_key]:
                    raw_brackets_by_pos[pos_key].append(n.raw_bracket)
        
        # ---- 计算分音标注 ----
        labels = self._compute_subdivision_labels()
        if not labels:
            return
        
        try:
            font = ImageFont.load_default(size=16)
        except:
            font = ImageFont.load_default()
        
        subdiv_color = (220, 190, 50)        # 金色（分音等级）
        bracket_color = (120, 120, 130)      # 灰色（[]原始值）
        
        drawn_positions = set()
        
        for beat_pos in sorted(labels.keys()):
            label = labels[beat_pos]
            if not label:
                continue
            pos_key = round(beat_pos, 4)
            if pos_key in drawn_positions:
                continue
            drawn_positions.add(pos_key)
            
            unit_idx = int(beat_pos // self.vsc)
            if unit_idx >= self.total_units:
                unit_idx = self.total_units - 1
            
            y = self._bar_to_pixel_y_in_unit(beat_pos, unit_idx)
            x1, _ = self.get_unit_x_range(unit_idx)
            
            base_x = x1 + self.unit_w + 10
            
            # 黄色：分音等级
            self.draw.text((base_x, y - 10), label, fill=subdiv_color, font=font)
            
            # 灰色：[] 原始值（放在黄色右侧）
            pos_raw = round(beat_pos, 6)
            if pos_raw in raw_brackets_by_pos:
                bracket_text = ",".join(raw_brackets_by_pos[pos_raw])
                bracket_x = base_x + 24
                self.draw.text((bracket_x, y - 10), bracket_text, fill=bracket_color, font=font)
    # ---- Firework 特效 ----
    def _draw_firework_effect(self, note):
        """绘制烟花特效：半圆形放射状彩虹渐变
        触发条件：note.firework == True
        渲染层次：网格线上方，hold/slide线/贴图下方
        
        设计：
        - 半圆开口向上（向y减小方向）
        - 圆心 = note位置（touchhold在结束位置，其他在起始位置）
        - 半径 = 2个轨道宽度
        - 彩虹色HSL 0→300° 按角度映射
        - 透明度：中心255，边缘0
        - 边缘截断：超过轨道8(column=0)左侧 / 轨道1(column=7)右侧
        """
        # touchhold 的烟花在长条结束位置触发，其他在起始位置
        if note.type == "touchhold":
            beat_pos = note.end_beat_pos
        else:
            beat_pos = note.beat_pos
        x, y, unit_idx = self.bar_to_pixel(beat_pos, note.column)
        
        unit_start_x, _ = self.get_unit_x_range(unit_idx)
        track_step = TRACK_WIDTH + TRACK_PADDING
        
        # 圆心在轨道中心
        cx = x + TRACK_WIDTH // 2
        
        # 半径 = 2个轨道宽度
        radius = int(2 * track_step)
        
        # 左右边界截断（轨道8左边界，轨道1右边界）
        left_clip = unit_start_x
        right_clip = unit_start_x + 8 * track_step  # 8轨道的右边界
        
        # 创建临时RGBA图像（包含整个烟花区域）
        # 区域：以(cx, y)为中心，radius为半径的正方形
        firework_size = radius * 2
        fw = Image.new("RGBA", (firework_size, firework_size), (0, 0, 0, 0))
        fw_draw = ImageDraw.Draw(fw)
        
        # 烟花在临时图中的位置：圆心在临时图中心
        center_fw = (firework_size // 2, firework_size // 2)
        
        # 遍历所有像素，绘制彩虹渐变半圆
        for py in range(firework_size):
            for px in range(firework_size):
                dx = px - center_fw[0]
                dy = py - center_fw[1]
                dist = math.sqrt(dx * dx + dy * dy)
                
                if dist > radius or dist < 1:
                    continue
                
                # 只绘制上半圆（dy < 0 = 向上，y减小方向）
                # 注意：图片y向下增大，但谱面y向上是过去时间
                # 开口向上 → 在图像坐标中dy应为负（上半部分）
                if dy >= 0:
                    continue
                
                # 角度：从 -90°（最左）到 90°（最右）
                angle = math.degrees(math.atan2(-dy, dx))  # 0°=右, 90°=上
                # 映射到HSL：角度-90~90 → HSL 0~300°
                # -90°→0(红), -45°→75(绿), 0°→150(青), 45°→225(蓝), 90°→300(紫)
                h = (angle + 90) / 180.0 * 300.0 / 360.0
                h = max(0, min(1, h))
                
                # 透明度：中心255，边缘0（0→radius线性）
                alpha = int(255 * (1 - dist / radius))
                
                # HSL → RGB
                r, g, b = colorsys.hls_to_rgb(h, 0.55, 0.9)
                fw.putpixel((px, py), (int(r * 255), int(g * 255), int(b * 255), alpha))
        
        # 计算在主画布上的粘贴位置（相对于临时图中心对齐音符位置）
        paste_x = cx - center_fw[0]
        paste_y = int(y - center_fw[1])
        
        # 对临时图进行边界截断（只保留轨道范围内的部分）
        # 计算主画布上的有效区域
        canvas_paste_x = int(max(left_clip, paste_x))
        canvas_paste_y = paste_y
        canvas_right = int(min(right_clip, paste_x + firework_size))
        canvas_bottom = paste_y + firework_size
        
        # 计算临时图中的对应裁剪区域
        crop_left = canvas_paste_x - paste_x
        crop_top = 0
        crop_right = canvas_right - paste_x
        crop_bottom = firework_size
        
        if crop_right <= crop_left:
            return
        
        # 裁剪
        fw_cropped = fw.crop((int(crop_left), int(crop_top), int(crop_right), int(crop_bottom)))
        
        # 粘贴到主画布
        self.img.paste(fw_cropped, (int(canvas_paste_x), int(canvas_paste_y)), mask=fw_cropped.split()[-1])
    
    def _paste_note(self, img, x, y):
        """粘贴 note 贴图到画布 (居中)"""
        if img is None:
            return
        cx = x + (TRACK_WIDTH - NOTE_SIZE) // 2
        cy = y - NOTE_SIZE // 2
        self.img.paste(img, (int(cx), int(cy)), mask=img.split()[-1])
    
    def _draw_tap(self, note):
        """绘制 Tap 音符（EX叠加）"""
        x, y, _ = self.bar_to_pixel(note.beat_pos, note.column)
        
        # 基础贴图
        if note.break_note:
            base_name = "tap_break.png"
        elif note.each:
            base_name = "tap_each.png"
        else:
            base_name = "tap.png"
        
        tex = self.textures.get(base_name, (NOTE_SIZE, NOTE_SIZE))
        self._paste_note(tex, x, y)
        
        # EX 叠加层（在基础贴图上再盖一层ex）
        if note.ex:
            ex_tex = self.textures.get("tap_ex.png", (NOTE_SIZE, NOTE_SIZE))
            if ex_tex:
                self._paste_note(ex_tex, x, y)
    
    # ---- Hold 绘制 ----
    
    def _get_hold_tex_names(self, note):
        """根据修饰选择 hold 的头帽和 body 贴图名（break优先，EX作为叠加层）"""
        if note.break_note:
            return "hold_break.png", "hold_break_body.png"
        elif note.each:
            return "hold_each.png", "hold_each_body.png"
        else:
            return "hold.png", "hold_body.png"
    
    def _draw_hold_bar_only(self, note):
        """只画 hold 条（跨Unit分割，每个Unit渲染对应的段）"""
        # 无时长（无[]）→ 直接贴完整 hold.png
        if note.end_beat_pos <= note.beat_pos:
            x, ys, _ = self.bar_to_pixel(note.beat_pos, note.column)
            head_name, _ = self._get_hold_tex_names(note)
            tex = self.textures.get(head_name, (NOTE_SIZE, NOTE_SIZE))
            self._paste_note(tex, x, ys)
            if note.ex:
                ex_tex = self.textures.get("hold_ex.png", (NOTE_SIZE, NOTE_SIZE))
                if ex_tex:
                    self._paste_note(ex_tex, x, ys)
            return
        
        segments = self._get_unit_segments(note.beat_pos, note.end_beat_pos, note.column)
        if not segments:
            return
        
        head_name, body_name = self._get_hold_tex_names(note)
        
        for idx, (unit_idx, seg_start, seg_end, seg_x) in enumerate(segments):
            is_first = (idx == 0)
            is_last = (idx == len(segments) - 1)
            
            ys_seg = self._bar_to_pixel_y_in_unit(seg_start, unit_idx)
            ye_seg = self._bar_to_pixel_y_in_unit(seg_end, unit_idx)
            
            self._draw_hold_three_parts_seg(seg_x, ys_seg, ye_seg, head_name, body_name,
                                            is_first, is_last)
            
            # EX叠加
            if note.ex:
                self._draw_hold_three_parts_seg(seg_x, ys_seg, ye_seg,
                                                "hold_ex.png", "hold_ex_body.png",
                                                is_first, is_last)
    
    def _draw_hold_three_parts_seg(self, x, ys, ye, head_name, body_name,
                                    draw_start_cap, draw_end_cap):
        """绘制 hold 在单个Unit内的一段
        
        跨多个Unit时，只有起始段画下半帽，只有结束段画上半帽，
        中间段只画 body 拉伸。
        """
        head_tex = self.textures.get(head_name)
        body_tex = self.textures.get(body_name)
        if head_tex is None and body_tex is None:
            return
        
        w_native = 122
        h_native = 122
        cut = 61
        
        scale = NOTE_SIZE / w_native
        half_top = int(round((cut + 1) * scale))
        half_bottom = int(round((h_native - cut) * scale))
        
        rx = x + (TRACK_WIDTH - NOTE_SIZE) // 2
        
        y_start = max(ys, ye)
        y_end = min(ys, ye)
        
        # 上半帽（在y_end上方，只有是结束段才画）
        if head_tex and draw_end_cap:
            top_cap = head_tex.crop((0, 0, w_native, cut + 1))
            top_cap = top_cap.resize((NOTE_SIZE, half_top), Image.Resampling.LANCZOS)
            paste_y = int(y_end) - half_top
            self.img.paste(top_cap, (rx, paste_y), mask=top_cap.split()[-1])
        
        # 下半帽（在y_start下方，只有是起始段才画）
        if head_tex and draw_start_cap:
            bottom_cap = head_tex.crop((0, cut, w_native, h_native))
            bottom_cap = bottom_cap.resize((NOTE_SIZE, half_bottom), Image.Resampling.LANCZOS)
            paste_y = int(y_start)
            self.img.paste(bottom_cap, (rx, paste_y), mask=bottom_cap.split()[-1])
        
        # Body 拉伸（严格填充 y_end 到 y_start 之间，不含帽子）
        if body_tex:
            body_top = int(y_end)
            body_bottom = int(y_start)
            if body_bottom > body_top:
                body_h = body_bottom - body_top
                body_img = body_tex.resize((NOTE_SIZE, body_h), Image.Resampling.LANCZOS)
                self.img.paste(body_img, (rx, body_top), mask=body_img.split()[-1])
    
    def _draw_hold_head(self, note):
        """hold 头部覆盖（已废弃，EX叠加已合入 _draw_hold_bar_only）"""
        pass

    
    # ---- Touch 绘制 ----
    
    def _get_touches_at_position(self, beat_pos, column, exclude_note=None):
        """获取同一 (beat_pos, column) 位置上的所有 touch/touchhold 音符
        
        Returns: list of touch/touchhold notes at this position
        """
        result = []
        for n in self.notes:
            if n.type not in ("touch", "touchhold"):
                continue
            if exclude_note and n is exclude_note:
                continue
            if abs(n.beat_pos - beat_pos) < 0.001 and n.column == column:
                result.append(n)
        return result
    
    def _make_touch_label(self, notes_at_pos):
        """生成合并的touch标签（含firework标记）
        
        规则：
        - 收集所有传感器ID，去重排序
        - 如果firework=True，在ID后加f
        - 用逗号分隔
        """
        sensors = []
        seen = set()
        for n in notes_at_pos:
            if n.sensor and n.sensor not in seen:
                seen.add(n.sensor)
                label = n.sensor
                if n.firework:
                    label += "f"
                sensors.append(label)
        return ",".join(sensors)
    
    def _draw_touch(self, note):
        """绘制 Touch 音符（含重叠合并 + firework标注）"""
        # 先检查是否已渲染过此位置（防止重复）
        pos_key = (round(note.beat_pos, 6), note.column)
        if pos_key in self._touch_rendered:
            return
        
        x, y, _ = self.bar_to_pixel(note.beat_pos, note.column)
        
        # 收集同一位置所有 touch/touchhold 音符
        all_touches = [note] + self._get_touches_at_position(note.beat_pos, note.column, note)
        all_touches.sort(key=lambda n: 0 if n.type == "touchhold" else 1)  # touchhold优先渲染（下层）
        
        has_touchhold = any(n.type == "touchhold" for n in all_touches)
        has_touch = any(n.type == "touch" for n in all_touches)
        has_each = any(n.each for n in all_touches)
        
        # 如果有touchhold，先画touchhold图标（下层）
        if has_touchhold:
            tex_th = self.textures.get("touchhold.png", (NOTE_SIZE, NOTE_SIZE))
            self._paste_note(tex_th, x, y)
            # touchhold的EX叠加
            if any(n.ex for n in all_touches if n.type == "touchhold"):
                ex_tex = self.textures.get("touch_each.png", (NOTE_SIZE, NOTE_SIZE))
                if ex_tex:
                    self._paste_note(ex_tex, x, y)
        
        # 如果有touch，画touch图标（上层，覆盖touchhold图标重叠）
        if has_touch:
            # touch图标选择：firework不影响图标，each影响
            base_name = "touch_each.png" if has_each else "touch.png"
            tex_t = self.textures.get(base_name, (NOTE_SIZE, NOTE_SIZE))
            self._paste_note(tex_t, x, y)
            # touch的EX叠加
            if any(n.ex for n in all_touches if n.type == "touch"):
                ex_tex = self.textures.get("touch_each.png", (NOTE_SIZE, NOTE_SIZE))
                if ex_tex:
                    self._paste_note(ex_tex, x, y)
        
        # 右上角标注合并的传感器ID
        if any(n.sensor for n in all_touches):
            try:
                font = ImageFont.load_default(size=12)
            except:
                font = ImageFont.load_default()
            label = self._make_touch_label(all_touches)
            self.draw.text((x + TRACK_WIDTH - 12, y - NOTE_SIZE // 2 - 5),
                          label, fill=(255, 255, 100), font=font)
        
        # 标记此位置已渲染
        self._touch_rendered.add(pos_key)
    
    def _draw_touchhold_bar_only(self, note):
        """Touch Hold 长条（渐变条，跨Unit分割）"""
        if note.end_beat_pos <= note.beat_pos:
            return
        
        segments = self._get_unit_segments(note.beat_pos, note.end_beat_pos, note.column)
        if not segments:
            return
        
        # 渐变 4 色（从下到上）
        gradient_colors = [
            (44, 166, 224), 
            (13, 172, 103),    
            (250, 237, 0),     
            (233, 85, 19),     
        ]
        
        # 宽度 = 图标宽度的 1/3
        bar_w = NOTE_SIZE // 3
        
        for unit_idx, seg_start, seg_end, seg_x in segments:
            ys_seg = self._bar_to_pixel_y_in_unit(seg_start, unit_idx)
            ye_seg = self._bar_to_pixel_y_in_unit(seg_end, unit_idx)
            
            rx = seg_x + (TRACK_WIDTH - bar_w) // 2
            
            y_start = max(ys_seg, ye_seg) - 10
            y_end = min(ys_seg, ye_seg)
            
            bar_top = int(y_end)
            bar_bottom = int(y_start + NOTE_SIZE // 2)
            
            h = bar_bottom - bar_top
            if h <= 0:
                continue
            
            band_count = len(gradient_colors) - 1
            band_h = h // band_count
            remainder = h % band_count
            
            cur_y = bar_top
            for i in range(band_count):
                c0 = gradient_colors[i]
                c1 = gradient_colors[i + 1]
                b_h = band_h + (remainder if i == band_count - 1 else 0)
                for py in range(b_h):
                    t = py / max(b_h - 1, 1)
                    r = int(c0[0] + (c1[0] - c0[0]) * t)
                    g = int(c0[1] + (c1[1] - c0[1]) * t)
                    b = int(c0[2] + (c1[2] - c0[2]) * t)
                    self.draw.line([(rx, cur_y), (rx + bar_w - 1, cur_y)], fill=(r, g, b))
                    cur_y += 1

    
    def _draw_touchhold_head(self, note):
        """Touch Hold 头部（如果已由_draw_touch渲染则跳过）"""
        pos_key = (round(note.beat_pos, 6), note.column)
        if pos_key in self._touch_rendered:
            return
        
        x, y, _ = self.bar_to_pixel(note.beat_pos, note.column)
        
        # 收集同一位置所有 touch/touchhold 音符
        all_touches = [note] + self._get_touches_at_position(note.beat_pos, note.column, note)
        all_touches.sort(key=lambda n: 0 if n.type == "touchhold" else 1)
        
        has_touchhold = any(n.type == "touchhold" for n in all_touches)
        has_touch = any(n.type == "touch" for n in all_touches)
        
        # touchhold图标
        if has_touchhold:
            tex_th = self.textures.get("touchhold.png", (NOTE_SIZE, NOTE_SIZE))
            self._paste_note(tex_th, x, y)
        
        # touch图标（如果有）
        if has_touch:
            has_each = any(n.each for n in all_touches if n.type == "touch")
            base_name = "touch_each.png" if has_each else "touch.png"
            tex_t = self.textures.get(base_name, (NOTE_SIZE, NOTE_SIZE))
            self._paste_note(tex_t, x, y)
        
        # 合并标签
        if any(n.sensor for n in all_touches):
            try:
                font = ImageFont.load_default(size=12)
            except:
                font = ImageFont.load_default()
            label = self._make_touch_label(all_touches)
            self.draw.text((x + TRACK_WIDTH - 12, y - NOTE_SIZE // 2 - 5),
                          label, fill=(255, 255, 100), font=font)
        
        self._touch_rendered.add(pos_key)

    
    # ---- Slide 绘制 ----
    
    def _has_multi_slide(self, note):
        """检查同一 beat_pos+column 是否有多个 slide（来自 * 连接）"""
        for n in self.notes:
            if (n != note and n.type == "slide" and 
                abs(n.beat_pos - note.beat_pos) < 0.001 and n.column == note.column):
                return True
        return False
    
    def _has_same_slide_timing(self, note):
        """检查同一位置的所有 slide 是否都有一致的 timing（/ 两侧延迟一致）"""
        if not note.slide_timing:
            return False
        target_tame = note.slide_timing.get('tame_beats', 0)
        target_slide = note.slide_timing.get('slide_beats', 0)
        for n in self.notes:
            if (n != note and n.type == "slide" and 
                abs(n.beat_pos - note.beat_pos) < 0.001 and n.column == note.column):
                if not n.slide_timing:
                    return False
                if (n.slide_timing.get('tame_beats', 0) != target_tame or
                    n.slide_timing.get('slide_beats', 0) != target_slide):
                    return False
        return True
    
    def _get_slide_tex_name(self, note):
        """滑条贴图选择：break > *的each > /延迟一致each > slide_star_each > 普通
        
        决策树：
        break > 优先级最高
        *（多slide）→ 双星，滑条显示each
        /（each）→ 只有所有slide延迟一致才用slide_each
        slide_star_each（混合组中slide）→ 星星each但滑条不用each
        """
        if note.break_note:
            return "slide_break.png"
        
        # 多slide（*）→ each样式，优先于 / 组的 slide_star_each
        if self._has_multi_slide(note):
            return "slide_each.png"
        
        if note.each:
            # 有 / 时：只有所有slide延迟一致才用each
            if self._has_same_slide_timing(note):
                return "slide_each.png"
            return "slide.png"
        
        # slide_star_each 时：混合 / 组中slide，星星要each但滑条不用each
        if note.slide_star_each:
            return "slide.png"
        
        return "slide.png"

    
    def _draw_slide_line_only(self, note):
        """只画 slide 的线条部分（虚线+滑条贴图，跨Unit分割渲染）
        
        滑条采用参数化全局线 + 逐Unit裁剪的方式：
        从 (x_start, y_start) 到 (x_end, y_end) 计算完整线段，
        每个Unit只渲染落在自己 y 范围内的那一小段。
        """
        tame_end_beat = note.beat_pos
        slide_end_beat = note.end_beat_pos
        
        if note.slide_timing:
            tame_beats = note.slide_timing.get('tame_beats', 0)
            tame_end_beat = note.beat_pos + tame_beats
        
        # 滑条贴图
        slide_tex_name = self._get_slide_tex_name(note)
        slide_img = self.textures.get(slide_tex_name)
        if slide_img is None:
            slide_img = self.textures.get("slide.png")
        
        # 缩放滑条贴图
        slide_resized = None
        if slide_img:
            s_w, s_h = slide_img.size
            scale = NOTE_SIZE / s_h
            new_w = max(1, int(s_w * scale))
            slide_resized = slide_img.resize((new_w, NOTE_SIZE), Image.Resampling.LANCZOS)
        
        # 起点坐标
        x_start, ys, _ = self.bar_to_pixel(note.beat_pos, note.column) 
        tame_end_y = self.bar_to_pixel_y(tame_end_beat)
        
        # ---- 虚线段（tame延迟）：垂直虚线，跨Unit分割 ----
        if tame_end_beat > note.beat_pos:
            dash_segments = self._get_unit_segments(note.beat_pos, tame_end_beat, note.column)
            for unit_idx, dash_seg_start, dash_seg_end, dash_seg_x in dash_segments:
                dash_ys = self._bar_to_pixel_y_in_unit(max(note.beat_pos, dash_seg_start), unit_idx)
                dash_ye = self._bar_to_pixel_y_in_unit(min(tame_end_beat, dash_seg_end), unit_idx)
                if dash_ys == dash_ye:
                    continue
                
                cx = dash_seg_x + TRACK_WIDTH // 2
                dash_y1 = min(dash_ys, dash_ye)
                dash_y2 = max(dash_ys, dash_ye)
                dash_len = 6
                gap_len = 4
                dy = dash_y1
                while dy < dash_y2:
                    self.draw.line([(cx, dy), (cx, min(dy + dash_len, dash_y2))], 
                                  fill=SLIDE_TAME_DASH_COLOR, width=2)
                    dy += dash_len + gap_len
        
        # ---- 滑条段：每个Unit使用自己的x坐标，独立渲染该段 ----
        if note.end_beat_pos > tame_end_beat and slide_resized:
            start_beat = tame_end_beat
            end_beat = slide_end_beat
            total_beat_range = end_beat - start_beat
            
            slide_segments = self._get_unit_segments(start_beat, end_beat, note.column)
            
            if total_beat_range > 0:
                # 列偏移量（终点列 - 起点列 的像素偏移）
                col_dx = (note.end_column - note.column) * (TRACK_WIDTH + TRACK_PADDING)
                
                for u_idx, seg_start, seg_end, seg_x in slide_segments:
                    # 每个Unit使用自己的seg_x作为x基准，加上列偏移的进度插值
                    # 这样跨Unit时x坐标不会受Unit间padding影响
                    t_start = (seg_start - start_beat) / total_beat_range
                    t_end = (seg_end - start_beat) / total_beat_range
                    
                    start_cx = seg_x + TRACK_WIDTH // 2 + col_dx * t_start
                    end_cx = seg_x + TRACK_WIDTH // 2 + col_dx * t_end
                    
                    sy_seg = self._bar_to_pixel_y_in_unit(seg_start, u_idx)
                    ey_seg = self._bar_to_pixel_y_in_unit(seg_end, u_idx)
                    
                    seg_dx = end_cx - start_cx
                    seg_dy = ey_seg - sy_seg
                    seg_len = math.sqrt(seg_dx * seg_dx + seg_dy * seg_dy)
                    
                    if seg_len <= 10:
                        continue
                    
                    angle = math.degrees(math.atan2(seg_dy, -seg_dx))
                    step = 20
                    num_steps = max(1, int(seg_len / step))
                    
                    for i in range(num_steps + 1):
                        t = i / num_steps
                        px = start_cx + seg_dx * t
                        py = sy_seg + seg_dy * t
                        
                        rotated = slide_resized.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
                        rw, rh = rotated.size
                        
                        paste_x = int(px - rw // 2)
                        paste_y = int(py - rh // 2)
                        self.img.paste(rotated, (paste_x, paste_y), mask=rotated.split()[-1])
    
    def _draw_slide_head(self, note):
        """画 slide 的起点 Star + 形状标注
        break > each > 普通（EX作为叠加层）
        多 slide（*）用双星，无星星的不画
        """
        x, ys, _ = self.bar_to_pixel(note.beat_pos, note.column)
        
        # 起点 Star
        if not note.no_start_star:
            has_multi = self._has_multi_slide(note)
            
            # 先选 base 贴图（EX作为叠加层，不参与base选择）
            if has_multi:
                if note.slide_start_break:
                    star_name = "star_double_break.png"
                elif note.each or note.slide_star_each:
                    star_name = "star_double_each.png"
                else:
                    star_name = "star_double.png"
            else:
                if note.slide_start_break:
                    star_name = "star_break.png"
                elif note.each or note.slide_star_each:
                    star_name = "star_each.png"
                else:
                    star_name = "star.png"
            
            star_tex = self.textures.get(star_name, (NOTE_SIZE, NOTE_SIZE))
            self._paste_note(star_tex, x, ys)
            
            # EX 叠加层：在基础星星上再贴 EX
            if note.ex:
                ex_name = "star_double_ex.png" if has_multi else "star_ex.png"
                ex_tex = self.textures.get(ex_name, (NOTE_SIZE, NOTE_SIZE))
                if ex_tex:
                    self._paste_note(ex_tex, x, ys)
        
        # 形状标注（多slide只合并显示一次：取当前note和第一个兄弟的标签）
        if note.shape:
            has_multi = self._has_multi_slide(note)
            
            # 多slide时只有第一个（index最小）渲染标签，避免重叠
            if has_multi:
                my_idx = self.notes.index(note)
                siblings = [n for n in self.notes if n != note and n.type == "slide" and
                           abs(n.beat_pos - note.beat_pos) < 0.001 and n.column == note.column]
                if any(self.notes.index(s) < my_idx for s in siblings):
                    return  # 不是第一个，跳过标签渲染
            
            try:
                font = ImageFont.load_default(size=18)
            except:
                font = ImageFont.load_default()
            
            if has_multi:
                siblings = [n for n in self.notes if n != note and n.type == "slide" and
                           abs(n.beat_pos - note.beat_pos) < 0.001 and n.column == note.column]
                siblings_sorted = sorted(siblings, key=lambda n: self.notes.index(n))
                labels = [f"{note.shape}{note.end_btn}"]
                if siblings_sorted:
                    first_sibling = siblings_sorted[0]
                    labels.append(f"{first_sibling.shape}{first_sibling.end_btn}")
                shape_text = ",".join(labels)
            else:
                shape_text = note.shape
            
            self.draw.text((x + TRACK_WIDTH - 8, ys - NOTE_SIZE // 2 - 8),
                          shape_text, fill=(255, 200, 100), font=font)


def render_chart(notes, bpm_events, output="output.png", first_offset=0.0, song_info=None, bg_img_path=None):
    """渲染入口
    
    Args:
        notes: 音符列表
        bpm_events: BPM 事件列表
        output: 输出图片路径
        first_offset: 偏移秒数（&first），用于校准时间显示
        song_info: dict {title, artist, designer, diff_name} 歌曲信息
        bg_img_path: 背景图路径（可选）
    """
    canvas = ChartCanvas(notes, bpm_events, first_offset, song_info, bg_img_path)
    img = canvas.render()
    output = str(output).strip()
    img.save(output)
    print(f"✅ 图片生成完成: {output}")
    return output
