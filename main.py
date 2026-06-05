"""
Simai 谱面可视化渲染工具 - 命令行入口

支持三种输入：
  -t / --text     直接输入 simai 文本
  -f / --file     .simai 或 .txt 谱面文件
  -z / --zip      压缩包（含 maidata.txt + bg）

用法:
  python main.py -t "(120){4}1,3,5,7,2h[4:1],4,6,8," -o test.png
  python main.py -f input.simai -o output.png
  python main.py -z "Fine Logic no pv.zip" -o output.png
"""

import sys
import argparse
from pathlib import Path

# 限制最大音符数
MAX_NOTES = 3000


def main():
    parser = argparse.ArgumentParser(
        description="Simai 谱面可视化渲染工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py -t "(120){4}1,3,5,7,2h[4:1],4,6,8," -o test.png
  python main.py -f input.simai -o output.png
  python main.py -z "Fine Logic no pv.zip" -o output.png
        """
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-t", "--text", type=str, help="Simai 谱面文本（直接输入）")
    group.add_argument("-f", "--file", type=str, help="Simai 谱面文件路径 (.txt)")
    group.add_argument("-z", "--zip", type=str, help="压缩包路径 (含 maidata.txt + bg)")
    
    parser.add_argument("-o", "--output", type=str, default="output.png", help="输出图片路径 (默认: output.png)")
    
    args = parser.parse_args()
    
    # 三个分支：文本 / 文件 / 压缩包
    if args.text:
        # ---- 分支 A: 直接文本 ----
        chart_text = args.text
        print(f"📝 输入谱面文本:\n{chart_text}\n")
        
        from simai_parser import SimaiParser
        parser_obj = SimaiParser(chart_text)
        notes = parser_obj.parse()
        bpm_events = parser_obj.bpm_events
        first_offset = 0.0
        song_info = None
        bg_img_path = None
        
    elif args.file:
        # ---- 分支 B: 文件 ----
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"❌ 找不到文件: {file_path}")
            sys.exit(1)
        
        file_content = file_path.read_text(encoding='utf-8')
        print(f"📝 从文件加载: {file_path}")
        
        # 尝试用 parse_maidata 解析（支持 & 元数据 + 多难度）
        from simai_maidx import parse_maidata, select_difficulty
        parsed = parse_maidata(file_content)
        
        diffs = parsed.get("difficulties", {})
        if diffs:
            # 有 & 元数据 → 多难度选择
            print(f"🎵 检测到 {len(diffs)} 个难度")
            chart_text, first_offset, song_info = select_difficulty(parsed)
            if chart_text is None:
                sys.exit(1)
            bg_img_path = None
        else:
            # 纯 simai 文本（无 & 元数据），直接解析
            chart_text = file_content
            first_offset = 0.0
            song_info = None
            bg_img_path = None
        
        from simai_parser import SimaiParser
        parser_obj = SimaiParser(chart_text)
        notes = parser_obj.parse()
        bpm_events = parser_obj.bpm_events
        
    else:
        # ---- 分支 C: 压缩包 ----
        zip_path = Path(args.zip)
        if not zip_path.exists():
            print(f"❌ 找不到压缩包: {zip_path}")
            sys.exit(1)
        
        from simai_maidx import handle_zip
        chart_text, bg_img_path, first_offset, song_info, cleanup_dir = handle_zip(zip_path)
        
        if chart_text is None:
            print("❌ 压缩包解析失败")
            sys.exit(1)
        
        print(f"\n📝 谱面文本 ({len(chart_text)} 字符):")
        print(f"   {chart_text[:100]}...")
        
        from simai_parser import SimaiParser
        parser_obj = SimaiParser(chart_text)
        notes = parser_obj.parse()
        bpm_events = parser_obj.bpm_events
    
    # ---- 公共渲染流程 ----
    note_count = parser_obj.get_note_count()
    print(f"🔢 解析到 {note_count} 个音符")
    
    if note_count > MAX_NOTES:
        print(f"❌ 音符数量 ({note_count}) 超过限制 ({MAX_NOTES})"
              f"，请减少音符后重试")
        sys.exit(1)
    
    # 输出解析详情
    print(f"\n📋 解析结果:")
    for n in notes[:20]:
        print(f"  {n}")
    if len(notes) > 20:
        print(f"  ... 共 {len(notes)} 个音符")
    print(f"\n📊 BPM 事件: {bpm_events}")
    
    # 渲染
    print(f"\n🎨 开始渲染...")
    from simai_render import render_chart
    try:
        output_path = render_chart(
            notes, bpm_events, args.output,
            first_offset=first_offset,
            song_info=song_info,
            bg_img_path=bg_img_path
        )
        print(f"\n✨ 渲染完成!")
        print(f"📁 输出文件: {output_path}")
    finally:
        # 清理临时目录（如果有）
        if args.zip:
            import shutil
            if cleanup_dir and cleanup_dir.exists():
                shutil.rmtree(cleanup_dir)
                print(f"\n🧹 临时目录已清理")


if __name__ == "__main__":
    main()
