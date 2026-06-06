"""
Simai 谱面语法解析器
将 Simai 文本解析为结构化的音符数据，用于后续渲染

支持语法：
  - 全局配置: (bpm){division}, {#seconds}
  - Tap: 1-8, 带 b(break)/x(EX) 修饰
  - Hold: 1h[x:y], 1h[#s]
  - Touch: A1, B3, C, D2, E4, 带 f(花火)/x(EX)/h(hold)修饰
  - Slide: F-shape-E[timing], 支持 * 连接多个终点
  - Each: / 分隔, 伪Each: ' 分隔
  - BPM变更: 曲中 (new_bpm)
  - SLIDE timing: [bpm#n], [bpm#x:y], [sec##sec], [x:y]
"""

import re
import math


class SimaiNote:
    """单个音符的数据结构"""
    def __init__(self):
        self.type = "tap"              # tap, hold, touch, touchhold, slide
        self.btn = 0                   # 1-8 for button number
        self.column = 0                # 渲染列索引 (8-btn)
        self.end_column = 0            # slide 终点列
        self.sensor = None             # Touch 传感器ID (A1, B3, C, etc.)
        self.break_note = False
        self.ex = False
        self.each = False
        self.pseudo_each = False
        self.slide_star_each = False   # slide在混合组中星星显示each，但身体不显示
        self.firework = False
        self.shape = None              # slide 形状
        self.end_btn = None            # slide 终点按钮
        self.multi_slides = []         # * 连接的多slide
        self.slide_start_break = False # Fb
        self.no_start_star = False     # F? or F!
        self.force_circle = False      # F@
        
        # 时长 (hold/slide)
        self.duration = None           # [x, y]
        self.duration_sec = None       # [#s]
        
        # Slide timing
        self.slide_timing = None       # dict: tame_beats, slide_beats
        
        # 原始 [] 括号内容
        self.raw_bracket = ""          # 比如 "4:1", "160#2", "8:1"
        
        # 计算出的时间位置 (以 bar 为单位)
        self.beat_pos = 0.0
        self.end_beat_pos = 0.0
        
    @property
    def is_slide(self):
        return self.type == "slide"
    
    @property
    def is_hold(self):
        return self.type in ("hold", "touchhold")
    
    @property
    def is_touch(self):
        return self.type in ("touch", "touchhold")
    
    def __repr__(self):
        if self.type == "slide":
            return f"<Slide {self.btn}{self.shape}{self.end_btn} @{self.beat_pos:.3f}>"
        elif self.type == "hold":
            return f"<Hold {self.btn} @{self.beat_pos:.3f}-{self.end_beat_pos:.3f}>"
        elif self.type == "touch":
            return f"<Touch {self.sensor} @{self.beat_pos:.3f}>"
        else:
            return f"<Tap {self.btn} @{self.beat_pos:.3f}>"


class SimaiParser:
    """Simai 语法解析器"""
    
    def __init__(self, text):
        self.text = text.strip()
        self.bpm = 120.0
        self.base_div = 1              # {1}=4分音符, {4}=16分音符
        self.seconds_per_beat = 0.125  # 60/bpm/4/4 ... 每拍(quarter)秒数
        self.notes = []
        self.bpm_events = []
        self.div_events = []           # {'beat': position, 'div': n}
        self.current_beat = 0.0        # 当前位置 (以 bar 为单位, 1.0=4拍)
        
    def parse(self):
        """主解析入口"""
        lines = self.text.split('\n')
        chart_text = ""
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line == 'E':
                break
            # 移除 &inote_1= 等前缀
            if '=' in line and not line.startswith('(') and not line.startswith('{') and not line.startswith('#'):
                line = line.split('=', 1)[1].strip()
            chart_text += line
        
        if not chart_text:
            return []
        
        self.bpm_events = []
        return self._parse_chart(chart_text)
    
    def _parse_chart(self, text):
        """解析连续谱面文本"""
        groups = text.split(',')
        # 保留空组（连续逗号）以正确推进节拍
        groups = [g.strip() for g in groups]
        
        self.current_beat = 0.0
        
        for group_text in groups:
            self._parse_note_group(group_text)
            # Simai 语义: {n} = 1/n 小节
            # {1}=1.0小节, {4}=0.25小节(1拍), {8}=0.125小节, {16}=0.0625小节
            self.current_beat += 1.0 / self.base_div
        
        return self.notes
    
    def _parse_note_group(self, text):
        """解析一个逗号前的音符组"""
        text = text.strip()
        if not text:
            return
        
        # 检查 BPM 变更 (120)
        bpm_match = re.match(r'^\((\d+\.?\d*)\)', text)
        if bpm_match:
            self.bpm = float(bpm_match.group(1))
            self.seconds_per_beat = 60.0 / self.bpm
            self.bpm_events.append({'beat': self.current_beat, 'bpm': self.bpm})
            text = text[bpm_match.end():].strip()
        
        # 检查分音符变更 {n}
        div_match = re.match(r'^\{(\d+)\}', text)
        if div_match:
            self.base_div = int(div_match.group(1))
            self.div_events.append({'beat': self.current_beat, 'div': self.base_div})
            text = text[div_match.end():].strip()
        
        # 检查秒数模式 {#s}
        sec_match = re.match(r'^\{#(\d+\.?\d*)\}', text)
        if sec_match:
            self.seconds_per_beat = float(sec_match.group(1))
            text = text[sec_match.end():].strip()
        
        if not text:
            # 纯元数据组（只含BPM/分音变更，无音符），不应推进节拍
            self.current_beat -= 1.0 / self.base_div
            return
        
        # Each 组 (/ 分隔)
        if '/' in text:
            notes_before = len(self.notes)
            parts = text.split('/')
            temp_notes = []
            for p in parts:
                note = self._parse_single_note(p.strip())
                if note:
                    note.beat_pos = self.current_beat
                    temp_notes.append(note)
            
            # 收集所有组内notes（包括多段slide等内部创建的）
            group_notes = list(temp_notes)
            internal_notes = self.notes[notes_before:]
            group_notes.extend(internal_notes)
            
            if not group_notes:
                return
            
            # 检查是否所有音符类型相同
            types = set(n.type for n in group_notes)
            all_same_type = len(types) == 1
            
            for note in group_notes:
                if all_same_type:
                    # 全部同类型 → 正常each
                    note.each = True
                else:
                    # 混合类型（如 tap+slide）：
                    # non-slide 音符标记 each，slide 只星星each但身体不each
                    if note.type == "slide":
                        note.each = False
                        note.slide_star_each = True
                    else:
                        note.each = True
            
            # 只添加 temp_notes 到 self.notes（internal_notes 已添加）
            for note in temp_notes:
                self.notes.append(note)
            return
        
        # 伪Each (' 分隔)
        if "'" in text:
            parts = text.split("'")
            for i, p in enumerate(parts):
                note = self._parse_single_note(p.strip())
                if note:
                    note.pseudo_each = True
                    note.beat_pos = self.current_beat + i * 0.00025
                    self.notes.append(note)
            return
        
        # 多 Slide (* 连接)
        if '*' in text:
            self._parse_multi_slide(text)
            return
        
        # 单个音符
        note = self._parse_single_note(text)
        if note:
            note.beat_pos = self.current_beat
            self.notes.append(note)
    
    def _parse_multi_segment_slide(self, text):
        """解析多段 Slide: 如 5v6<8[8:3] → 5→v→6 (0.1875b) + 6→<→8 (0.1875b)"""
        # 提取末尾 timing
        timing_str = None
        timing_match = re.search(r'\[(.*?)\]$', text)
        if timing_match:
            timing_str = timing_match.group(1)
            body = text[:timing_match.start()].strip()
        else:
            body = text.strip()
        
        # 提取起点按钮和修饰符
        m = re.match(r'^(\d+)([bBx]*)(.*)', body)
        if not m:
            return None
        
        start_btn = int(m.group(1))
        prefix = m.group(2) or ''
        rest = m.group(3).strip()
        
        # 解析所有 segment: [mods]shapeE[mods]
        segments = []
        while rest:
            seg_m = re.match(r'^([bBx]*)([-^< >vpqszVw]|pp|qq)(\d+)([bBx]*)(.*)', rest)
            if not seg_m:
                break
            mid = seg_m.group(1) or ''
            shape = seg_m.group(2)
            end_btn_str = seg_m.group(3)
            suf = seg_m.group(4) or ''
            rest = seg_m.group(5).strip()
            segments.append((shape, end_btn_str, mid + suf))
        
        if len(segments) < 2:
            return None  # 不足2段，不是多段 Slide
        
        # 解析总 timing，按段数均分
        timing = None
        if timing_str:
            timing = self._parse_slide_timing(timing_str)
        
        total_slide = timing['slide_beats'] if timing else 0
        per_segment_slide = total_slide / len(segments)
        
        # 为每段创建 note（每段顺序执行，后一段从前一段终点开始）
        current_btn = start_btn
        current_beat_pos = self.current_beat
        created = 0
        for i, (shape, end_btn_str, mods) in enumerate(segments):
            note = SimaiNote()
            note.type = "slide"
            note.btn = current_btn
            note.column = 8 - current_btn
            note.shape = shape
            note.end_btn = self._parse_slide_end_btn(shape, end_btn_str)
            note.end_column = 8 - note.end_btn
            
            # 星星break = b 在 prefix（起点修饰）
            if 'b' in prefix.lower():
                note.slide_start_break = True
            # 滑条break = b 在 mods（段内修饰）
            if 'b' in mods.lower():
                note.break_note = True
            if 'x' in (prefix + mods).lower():

                note.ex = True
            
            # 只有第一段有默认 4分音 tame 延迟和星星
            if i == 0:
                note.no_start_star = False  # 有星星
                note.slide_timing = {
                    'tame_beats': 0.25,
                    'slide_beats': per_segment_slide
                }
                note.beat_pos = current_beat_pos
                note.end_beat_pos = current_beat_pos + 0.25 + per_segment_slide
                current_beat_pos = current_beat_pos + 0.25 + per_segment_slide
            else:
                # 后续段：无星星、无 tame，直接继续滑动
                note.no_start_star = True   # 无星星
                note.slide_timing = {
                    'tame_beats': 0,
                    'slide_beats': per_segment_slide
                }
                note.beat_pos = current_beat_pos
                note.end_beat_pos = current_beat_pos + per_segment_slide
                current_beat_pos = current_beat_pos + per_segment_slide
            
            self.notes.append(note)
            current_btn = note.end_btn
            created += 1
        
        # break传播：多段Slide中任一段有break修饰，所有段都标记为break
        if created > 1:
            has_break = any(n.break_note for n in self.notes[-created:])
            if has_break:
                for n in self.notes[-created:]:
                    n.break_note = True
        
        return created


    def _parse_single_note(self, text):
        """解析单个音符"""
        text = text.strip()
        if not text:
            return None
        
        # --- 多段 Slide 检测 (在单段之前，避免误匹配) ---
        # 如 5v6<8[8:3], 4v3>1[8:3], 1-7v6-4[4:3]
        multi_result = self._parse_multi_segment_slide(text)
        if multi_result:
            return None  # 已在内部创建了多个 note，无需再添加

        # --- 多 Slide (* 连接) ---
        # 如 5bv2[5000:469]*v8[5000:469]
        # 该分支需要在 Each 组（/）的子部分中也能正确处理
        if '*' in text:
            self._parse_multi_slide(text)
            return None  # _parse_multi_slide 内部已添加了多个 note

        # --- Slide 检测 ---
        # 格式: F[mods]shape-E[mods][timing]
        # 例: 1bxw5b[8:1] → btn=1, mods=bx, shape=w, end=5, more_mods=b, timing=8:1
        slide_pattern = r'^(\d+)([bBx]*)([-^< >vpqszVw]|pp|qq)(\d+)([bBx]*)(?:\[(.*?)\])?$'
        slide_match = re.match(slide_pattern, text)
        if slide_match:
            return self._parse_slide(slide_match)
        
        # 无起点星 Slide: 数字?形状 或 数字!形状 或 数字@形状
        special_slide = re.match(r'^(\d+)([\?!@])([-^< >vpqszVw]|pp|qq)(\d+)([bBx]*)(?:\[(.*?)\])?$', text)
        if special_slide:
            return self._parse_slide_special(special_slide)
        
        # --- Touch 检测 ---
        # 格式: A1, B3, C, D2, E4, 带 f(花火)/h(hold)/x(EX) 修饰和 [duration]
        touch_match = re.match(r'^([A-E])(\d*)([fhx]*)(?:\[(.*?)\])?$', text)
        if touch_match:
            return self._parse_touch(touch_match)
        
        # --- Button 音符检测 (1-8) ---
        btn_match = re.match(r'^(\d+)([bhx]*)(?:\[(.*?)\])?$', text)
        if btn_match:
            return self._parse_button_note(btn_match)
        
        return None
    
    def _parse_slide_end_btn(self, shape, end_btn_str):
        """解析 slide 终点按钮，处理 V 形状（如 V15 → 终点=5）"""
        btn = int(end_btn_str)
        if shape and shape.lower() == 'v':
            # V 形状：V15 表示 V 形到 5，中间的 1 是填充
            btn = int(end_btn_str[-1])
        return btn

    def _parse_slide(self, match):
        """解析 Slide: F[mods]shape-E[mods][timing]
        
        b 在 prefix(第一个数字前)→星星break
        b 在 suffix(第二个数字后)→滑条break
        """
        note = SimaiNote()
        note.type = "slide"
        note.btn = int(match.group(1))
        note.column = 8 - note.btn
        
        prefix = match.group(2) or ''
        note.shape = match.group(3)
        note.end_btn = self._parse_slide_end_btn(note.shape, match.group(4))
        note.end_column = 8 - note.end_btn
        suffix = match.group(5) or ''
        
        # 星星 break = b 在 prefix
        if 'b' in prefix.lower():
            note.slide_start_break = True
        # 滑条 break = b 在 suffix
        if 'b' in suffix.lower():
            note.break_note = True
        if 'x' in (prefix + suffix).lower():
            note.ex = True
        
        timing_str = match.group(6)
        if timing_str:
            note.slide_timing = self._parse_slide_timing(timing_str)
            note.raw_bracket = timing_str
        
        # 计算终点时间位置
        if note.slide_timing:
            tame = note.slide_timing.get('tame_beats', 0)
            slide = note.slide_timing.get('slide_beats', 0)
            note.end_beat_pos = self.current_beat + tame + slide
        
        return note
    
    def _parse_slide_special(self, match):
        """解析特殊 Slide: ?(无起点星) !(强制) @(圆形起点)
        特殊slide只有 suffix 有 b标记（第二个数字后）
        """
        note = SimaiNote()
        note.type = "slide"
        note.btn = int(match.group(1))
        note.column = 8 - note.btn
        
        special = match.group(2)
        note.shape = match.group(3)
        note.end_btn = int(match.group(4))
        note.end_column = 8 - note.end_btn
        suffix = match.group(5) or ''
        
        if special == '?' or special == '!':
            note.no_start_star = True
        elif special == '@':
            note.force_circle = True
        
        # 特殊slide b 只出现在 suffix（星星标记？!强制取代了prefix）
        if 'b' in suffix.lower():
            note.break_note = True  # 滑条break
        if 'x' in suffix.lower():
            note.ex = True
        
        timing_str = match.group(6)
        if timing_str:
            note.slide_timing = self._parse_slide_timing(timing_str)
        
        if note.slide_timing:
            tame = note.slide_timing.get('tame_beats', 0)
            slide = note.slide_timing.get('slide_beats', 0)
            note.end_beat_pos = self.current_beat + tame + slide
        
        return note
    
    def _parse_multi_slide(self, text):
        """解析多 Slide (F * shape1 E1[timing] * shape2 E2[timing])
        
        b 在 prefix(起点) → 星星break
        b 在 suffix/终点 → 滑条break
        * 连接的多个 slide 都用 same_start（双星）
        """
        first_match = re.match(r'^(\d+)([bBx]*)(.*)$', text)
        if not first_match:
            return
        
        start_btn = int(first_match.group(1))
        prefix = first_match.group(2) or ''
        rest = first_match.group(3)
        
        # 用 * 分割多个 slide
        slide_parts = rest.split('*')
        
        for part in slide_parts:
            part = part.strip()
            slide_match = re.match(r'^([bBx]*)([-^< >vpqszVw]|pp|qq)(\d+)([bBx]*)(?:\[(.*?)\])?$', part)
            if not slide_match:
                continue
            
            note = SimaiNote()
            note.type = "slide"
            note.btn = start_btn
            note.column = 8 - start_btn
            note.shape = slide_match.group(2)
            note.end_btn = self._parse_slide_end_btn(note.shape, slide_match.group(3))
            note.end_column = 8 - note.end_btn

            mid = slide_match.group(1) or ''
            suffix = slide_match.group(4) or ''
            
            # 星星break = b 在 prefix
            if 'b' in prefix.lower():
                note.slide_start_break = True
            # 滑条break = b 在 mid 或 suffix
            if 'b' in (mid + suffix).lower():
                note.break_note = True
            if 'x' in (prefix + mid + suffix).lower():
                note.ex = True
            
            timing_str = slide_match.group(5)
            if timing_str:
                note.slide_timing = self._parse_slide_timing(timing_str)
            
            note.beat_pos = self.current_beat
            if note.slide_timing:
                tame = note.slide_timing.get('tame_beats', 0)
                slide = note.slide_timing.get('slide_beats', 0)
                note.end_beat_pos = self.current_beat + tame + slide
            
            self.notes.append(note)
    
    def _parse_touch(self, match):
        """解析 Touch 音符"""
        note = SimaiNote()
        sensor_letter = match.group(1)
        sensor_num = match.group(2) or ''
        mods = match.group(3) or ''
        
        note.sensor = sensor_letter + sensor_num
        
        if sensor_letter in ('A', 'B', 'D', 'E') and sensor_num:
            note.type = "touch"
            note.column = 8 - int(sensor_num)
        elif sensor_letter == 'C':
            note.type = "touch"
            note.column = 4
        else:
            return None
        
        if 'f' in mods:
            note.firework = True
        if 'x' in mods:
            note.ex = True
        if 'h' in mods:
            note.type = "touchhold"
            dur_str = match.group(4)
            if dur_str:
                note.duration, note.duration_sec = self._parse_duration(dur_str)
                note.end_beat_pos = self.current_beat + self._duration_to_beats(note)
                note.raw_bracket = dur_str
        
        return note
    
    def _parse_button_note(self, match):
        """解析 1-8 按钮音符"""
        note = SimaiNote()
        num = int(match.group(1))
        if num < 1 or num > 8:
            return None
        
        note.btn = num
        note.column = 8 - num
        
        mods = match.group(2) or ''
        
        if 'b' in mods:
            note.break_note = True
        if 'x' in mods:
            note.ex = True
        if 'h' in mods:
            note.type = "hold"
            dur_str = match.group(3)
            if dur_str:
                note.duration, note.duration_sec = self._parse_duration(dur_str)
                note.end_beat_pos = self.current_beat + self._duration_to_beats(note)
                note.raw_bracket = dur_str
        else:
            note.type = "tap"
        
        return note
    
    def _parse_duration(self, dur_str):
        """解析时长字符串: [x:y] 或 [#s]"""
        if not dur_str:
            return None, None
        if dur_str.startswith('#'):
            try:
                return None, float(dur_str[1:])
            except:
                return None, None
        if ':' in dur_str:
            parts = dur_str.split(':')
            try:
                return [int(parts[0]), int(parts[1])], None
            except:
                return None, None
        return None, None
    
    def _duration_to_beats(self, note):
        """将 duration 转换为 bar 数"""
        if note.duration:
            x, y = note.duration
            # [x:y] 时长 = 1 小节 × (y/x)
            return y / x
        elif note.duration_sec:
            return note.duration_sec / (self.seconds_per_beat * 4)
        return 0.0
    
    def _parse_slide_timing(self, timing_str):
        """
        解析 Slide timing 参数
        [bpm#n]      → tame=BPM基准, slide=n秒
        [bpm#x:y]    → tame=BPM基准, slide=x/y beat (at BPM)
        [sec##sec]   → tame=sec秒, slide=sec秒
        [x:y]        → 标准时长
        """
        result = {}
        
        # [sec##sec] 格式
        if '##' in timing_str:
            parts = timing_str.split('##')
            try:
                tame_sec = float(parts[0])
                slide_sec = float(parts[1])
                result['tame_beats'] = tame_sec / (self.seconds_per_beat * 4)
                result['slide_beats'] = slide_sec / (self.seconds_per_beat * 4)
            except:
                result['tame_beats'] = 0
                result['slide_beats'] = 0
            return result
        
        # [bpm#...] 格式
        if '#' in timing_str:
            parts = timing_str.split('#')
            try:
                slide_bpm = float(parts[0])
            except:
                slide_bpm = self.bpm
            
            rest = parts[1]
            bpm_ratio = self.bpm / slide_bpm
            
            tame_beats = 1.0 * bpm_ratio
            
            if ':' in rest:
                xy = rest.split(':')
                x, y = int(xy[0]), int(xy[1])
                slide_bars = y / x
                slide_beats = slide_bars * bpm_ratio
            else:
                slide_sec = float(rest)
                slide_beats = slide_sec / (self.seconds_per_beat * 4)
            
            result['tame_beats'] = tame_beats
            result['slide_beats'] = slide_beats
            return result
        
        # [x:y] 标准时长 — 默认 1 拍(0.25 bar) 延迟
        # 例如 [8:1] = 1/8 小节 = 0.125 小节滑动, 默认 1 拍延迟
        if ':' in timing_str:
            parts = timing_str.split(':')
            try:
                x, y = int(parts[0]), int(parts[1])
                total = y / x
            except:
                total = 0
            result['tame_beats'] = 0.25  # 默认 4分音(1拍)延迟
            result['slide_beats'] = total
            return result
        
        result['tame_beats'] = 0
        result['slide_beats'] = 0
        return result
    
    def get_note_count(self):
        """获取总音符数"""
        return len(self.notes)


def test():
    """简单测试"""
    test_text = """(120){4}
1,3,5,7,
2h[4:1],4,6,8,
3-7[160#2],
A1,B2,C,E4f,
"""
    parser = SimaiParser(test_text)
    notes = parser.parse()
    print(f"解析到 {len(notes)} 个音符:")
    for n in notes:
        print(f"  {n}")
    print(f"BPM事件: {parser.bpm_events}")


if __name__ == "__main__":
    test()
