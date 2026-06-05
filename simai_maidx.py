"""
maidx (maimaidx) 压缩包解析模块

支持从压缩包中提取 maidata.txt 和背景图，解析歌曲信息与谱面内容。
提供控制台交互选择多难度谱面。
"""

import re
import zipfile
import shutil
from pathlib import Path


def parse_maidata(text):
    """解析 maidata.txt 内容
    
    自动去除 BOM 头（\ufeff）。
    
    格式:
        &title=歌曲名
        &artist=作者
        &first=0.2          ← 偏移秒数（校准用）
        &des=通用设计者
        &lv_1=Easy          ← 1=蓝谱
        &des_1=设计者
        &inote_1=1,3,5,7,   ← simai 语法，遇到单独的 E 截断
        E
        &lv_5=Master
        &des_5=设计者
        &inote_5=...
    
    Returns:
        dict: {
            "title": "...",
            "artist": "...",
            "first": 0.2,
            "difficulties": {
                "1": {"lv": "Easy", "des": "...", "chart_text": "1,3,5,7,"},
                "5": {"lv": "Master", "des": "...", "chart_text": "..."}
            }
        }
    """
    # 去除 BOM 头
    if text and text[0] == '\ufeff':
        text = text[1:]
    
    result = {
        "title": "Unknown",
        "artist": "Unknown",
        "first": 0.0,
        "difficulties": {}
    }
    
    lines = text.split('\n')
    
    current_diff = None
    current_inote_lines = []
    in_inote = False
    
    for line in lines:
        line = line.rstrip('\r').strip()
        if not line:
            continue
        
        # 检测 &key=value 行
        kv_match = re.match(r'^&(\w+)=(.*)', line)
        if kv_match:
            # --- 遇到新的 & 行时，先保存上一个 inote 段（无需 E 也能自动结束） ---
            if in_inote and current_inote_lines:
                chart_text = ','.join(current_inote_lines)
                chart_text = chart_text.rstrip(',')
                if current_diff:
                    if current_diff not in result["difficulties"]:
                        result["difficulties"][current_diff] = {}
                    result["difficulties"][current_diff]["chart_text"] = chart_text
                in_inote = False
                current_diff = None
                current_inote_lines = []
            
            key = kv_match.group(1)
            value = kv_match.group(2).strip()
            
            if key == "title":
                result["title"] = value
            elif key == "artist":
                result["artist"] = value
            elif key == "first":
                try:
                    result["first"] = float(value)
                except:
                    result["first"] = 0.0
            elif key == "des":
                # 通用设计者
                if "difficulties" not in result:
                    result["difficulties"] = {}
            elif re.match(r'^lv_\d+$', key):
                # &lv_1=Easy 等
                diff_num = key.split('_')[1]
                if diff_num not in result["difficulties"]:
                    result["difficulties"][diff_num] = {}
                result["difficulties"][diff_num]["lv"] = value
            elif re.match(r'^des_\d+$', key):
                diff_num = key.split('_')[1]
                if diff_num not in result["difficulties"]:
                    result["difficulties"][diff_num] = {}
                result["difficulties"][diff_num]["des"] = value
            elif re.match(r'^inote_\d+$', key):
                # &inote_X= 开始一个谱面段
                diff_num = key.split('_')[1]
                current_diff = diff_num
                current_inote_lines = [value] if value else []
                in_inote = True
                continue
        else:
            # 不在 &inote 段内则跳过
            if not in_inote:
                continue
        
        # ---- 处理 &inote 内的内容 ----
        # 如果不以 & 开头，且当前在 inote 段中
        if in_inote and not line.startswith('&'):
            # 单独的 E 表示结束（显式 E 也走同样保存逻辑，但保留 E 检测不影响）
            if line == 'E':
                chart_text = ','.join(current_inote_lines)
                chart_text = chart_text.rstrip(',')
                if current_diff:
                    if current_diff not in result["difficulties"]:
                        result["difficulties"][current_diff] = {}
                    result["difficulties"][current_diff]["chart_text"] = chart_text
                in_inote = False
                current_diff = None
                current_inote_lines = []
            else:
                current_inote_lines.append(line)
    
    # 处理文件末尾没有 E 的情况
    if in_inote and current_inote_lines:
        chart_text = ','.join(current_inote_lines)
        chart_text = chart_text.rstrip(',')
        if current_diff:
            if current_diff not in result["difficulties"]:
                result["difficulties"][current_diff] = {}
            result["difficulties"][current_diff]["chart_text"] = chart_text
    
    # 清理没有 chart_text 的 diff 条目
    to_del = []
    for k, v in result["difficulties"].items():
        if "chart_text" not in v:
            to_del.append(k)
    for k in to_del:
        del result["difficulties"][k]
    
    return result


# 难度显示名称映射（用于控制台列表）
DIFFICULTY_NAMES = {
    "1": "Easy         [蓝]",
    "2": "Basic        [绿]",
    "3": "Advanced     [黄]",
    "4": "Expert       [红]",
    "5": "Master       [紫]",
    "6": "ReMaster     [白]",
}


def select_difficulty(parsed):
    """从解析结果中选择难度，控制台交互
    
    适用于文件（.simai/.txt）和压缩包两种来源。
    
    Args:
        parsed: parse_maidata() 的返回值
    
    Returns:
        (chart_text, first_offset, song_info) 或 (None, None, None)
            chart_text: simai 谱面文本（仅 inote 内容，不含 & 元数据）
            first_offset: float 偏移秒数
            song_info: dict {title, artist, designer, diff_name}
    """
    diffs = parsed.get("difficulties", {})
    if not diffs:
        print("❌ 未找到任何谱面难度")
        return None, None, None
    
    sorted_diff_nums = sorted(diffs.keys(), key=lambda x: int(x))
    
    # 多难度→控制台选择
    if len(sorted_diff_nums) > 1:
        print(f"\n🎵 {parsed['title']} - {parsed['artist']}")
        print(f"📊 检测到多个难度，请选择：")
        for i, diff_num in enumerate(sorted_diff_nums):
            d = diffs[diff_num]
            diff_name = DIFFICULTY_NAMES.get(diff_num, f"难度{diff_num}")
            des = d.get("des", "")
            des_str = f" | Designer: {des}" if des else ""
            chart_len = len(d.get("chart_text", ""))
            print(f"  [{i}] {diff_name}{des_str} ({chart_len}字符)")
        print()
        
        try:
            idx = int(input("输入数字选择: ").strip())
            selected_num = sorted_diff_nums[idx]
        except (ValueError, IndexError):
            selected_num = sorted_diff_nums[0]
            print(f"⚠️ 输入错误，默认选择第 0 项")
    else:
        selected_num = sorted_diff_nums[0]
    
    selected = diffs[selected_num]
    chart_text = selected.get("chart_text", "")
    first_offset = parsed.get("first", 0.0)
    song_info = {
        "title": parsed.get("title", "Unknown"),
        "artist": parsed.get("artist", "Unknown"),
        "designer": selected.get("des", ""),
        "diff_name": DIFFICULTY_NAMES.get(selected_num, f"难度{selected_num}").split("[")[0].strip(),
    }
    diff_lv_name = DIFFICULTY_NAMES.get(selected_num, f"难度{selected_num}")
    
    if not chart_text:
        print(f"❌ 难度 {selected_num} 的谱面内容为空")
        return None, None, None
    
    print(f"\n✅ 已选择: {diff_lv_name}")
    print(f"   谱面长度: {len(chart_text)} 字符")
    
    return chart_text, first_offset, song_info


def handle_zip(zip_path):
    """处理压缩包：解析 maidata.txt + 提取背景图
    
    多难度时控制台列表询问选择。
    
    Args:
        zip_path: Path 对象，压缩包路径
    
    Returns:
        (chart_text, bg_path, first_offset, song_info, cleanup_dir) 或 (None, *None)
            chart_text: simai 谱面文本
            bg_path: Path 背景图路径（或 None）
            first_offset: float 偏移秒数
            song_info: dict {title, artist, designer, diff_name}
            cleanup_dir: Path 临时目录（调用者负责清理）
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        print(f"❌ 找不到压缩包: {zip_path}")
        return None, None, None, None, None
    
    # 创建临时目录
    temp_dir = Path("./.tmp_cache_maidx")
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    
    bg_path = None
    chart_text = None
    first_offset = 0.0
    song_info = {"title": "Unknown", "artist": "Unknown", "designer": "", "diff_name": ""}
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            all_files = zf.namelist()
            
            # 找 maidata.txt
            maidata_name = None
            for f in all_files:
                if f.lower().endswith("maidata.txt"):
                    maidata_name = f
                    break
            # 如果没有 maidata.txt，找任何 .txt
            if not maidata_name:
                for f in all_files:
                    if f.lower().endswith(".txt"):
                        maidata_name = f
                        break
            
            if not maidata_name:
                print("❌ 压缩包内未找到 maidata.txt 或谱面文本文件")
                return None, None, None, None, None
            
            # 读取并解析 maidata
            maidata_content = zf.read(maidata_name).decode('utf-8', errors='ignore')
            parsed = parse_maidata(maidata_content)
            
            # 提取 maidata.txt 到临时目录（以备他用）
            tmp_maidata = temp_dir / Path(maidata_name).name
            with open(tmp_maidata, "wb") as f:
                f.write(zf.read(maidata_name))
            
            # 找背景图
            for ext in ['.png', '.jpg', '.jpeg', '.bmp']:
                for f in all_files:
                    if f.lower().endswith(ext) and ('bg' in f.lower() or 'back' in f.lower()):
                        bg_name = f
                        bg_path = temp_dir / Path(bg_name).name
                        with open(bg_path, "wb") as fout:
                            fout.write(zf.read(bg_name))
                        break
                if bg_path and bg_path.exists():
                    break
            # 没找到指定 bg 图，取第一张图片
            if not bg_path or not bg_path.exists():
                for f in all_files:
                    if f.lower().endswith(('.png', '.jpg', '.jpeg')):
                        bg_name = f
                        bg_path = temp_dir / Path(bg_name).name
                        with open(bg_path, "wb") as fout:
                            fout.write(zf.read(bg_name))
                        break
            
            # 选择难度（复用公共函数）
            chart_text, first_offset, song_info = select_difficulty(parsed)
            if chart_text is None:
                return None, None, None, None, None
            
    except Exception as e:
        print(f"❌ 处理压缩包时出错: {e}")
        import traceback
        traceback.print_exc()
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        return None, None, None, None, None
    
    return chart_text, bg_path, first_offset, song_info, temp_dir
