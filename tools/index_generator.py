#!/usr/bin/env python3
"""
dana 索引自动生成工具
从 frontmatter 自动生成/追加 MOC 索引条目，避免手动维护遗漏。

功能：
  --scan             扫描并预览将要追加的索引条目（不写入文件）
  --target <FILE>    仅更新指定 MOC 文件
  --all              更新所有 MOC 索引文件
  --section <NAME>   仅更新指定板块（人物志/心法/路径/方法论/案例/资源）
  --sync-docs        用真实文件数同步 docs/index.md 的统计卡片数字
  --dry-run          与 --sync-docs 连用，仅预览不写入

设计原则：
  - 保留 MOC 现有手工前言与说明文字
  - 仅在 "<!-- AUTO-INDEX-START -->" 与 "<!-- AUTO-INDEX-END -->" 标记之间追加
  - 如无标记，则在文件末尾追加新区块

用法：
  python tools/index_generator.py --scan
  python tools/index_generator.py --all
  python tools/index_generator.py --target 索引/全部笔记.md
"""

import os
import re
import sys
from pathlib import Path
from collections import defaultdict

# 复用 audit.py 的核心工具
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import (
    ROOT, IGNORE_DIRS, IGNORE_ROOT_FILES, find_md_files,
    parse_frontmatter, is_wealth_file
)

# 板块 → (路径前缀, 字段, 排序键)
SECTIONS = [
    ('人物志', '人物志', 'title'),
    ('富豪榜', '富豪榜', 'title'),
    ('心法与原则', '心法与原则', 'title'),
    ('成长路径', '成长路径', 'title'),
    ('方法论与框架', '方法论与框架', 'title'),
    ('案例研究', '案例研究', 'title'),
    ('资源库', '资源库', 'title'),
    ('AI研究案例库', None, 'title'),  # 根目录特殊文件
]

AUTO_START = '<!-- AUTO-INDEX-START -->'
AUTO_END = '<!-- AUTO-INDEX-END -->'


def collect_section_items(section_name):
    """收集指定板块的所有条目（带 frontmatter）"""
    items = []
    md_files = find_md_files()

    if section_name == 'AI研究案例库':
        # 根目录的两个 AI 研究案例库文件
        candidates = [
            ROOT / 'AI研究案例库.md',
            ROOT / 'AI研究案例库 2.md',
        ]
        for path in candidates:
            if path.exists():
                rel = path.relative_to(ROOT)
                content = path.read_text(encoding='utf-8')
                fm = parse_frontmatter(content)
                items.append({
                    'rel_path': rel,
                    'title': fm.get('title', path.stem) if fm else path.stem,
                    'date': fm.get('date', '') if fm else '',
                    'tags': fm.get('tags', []) if fm else [],
                })
        return items

    for rel in md_files:
        if not rel.parts or rel.parts[0] != section_name:
            continue
        content = (ROOT / rel).read_text(encoding='utf-8')
        fm = parse_frontmatter(content)
        if not fm:
            continue

        # 富豪榜排除非人物档案
        if section_name == '富豪榜' and rel.name.startswith('_'):
            continue
        if section_name == '富豪榜' and '综合分析' in rel.parts:
            continue

        items.append({
            'rel_path': rel,
            'title': fm.get('title', rel.stem),
            'date': fm.get('date', ''),
            'tags': fm.get('tags', []),
        })

    return items


def render_index_block(section_name, items):
    """渲染一个板块的索引条目（Markdown）"""
    if not items:
        return ''

    lines = [f'## {section_name}（自动生成）', '']

    # 按 title 排序
    items_sorted = sorted(items, key=lambda x: x['title'])

    for item in items_sorted:
        title = item['title']
        rel = item['rel_path']
        # rel 可能是 Path 或 str，统一处理
        if isinstance(rel, Path):
            rel_str = str(rel.with_suffix(''))
        else:
            rel_str = str(rel)
            if rel_str.endswith('.md'):
                rel_str = rel_str[:-3]
        # 用 wikilink 形式
        link = f'[[{rel_str}]]'
        date_str = ''
        if item['date']:
            if hasattr(item['date'], 'isoformat'):
                date_str = item['date'].isoformat()
            else:
                date_str = str(item['date'])[:10]
        date_suffix = f'（{date_str}）' if date_str else ''
        lines.append(f'- {link} — {title}{date_suffix}')

    lines.append('')
    lines.append(f'> 📊 共 {len(items)} 条 · 由 `tools/index_generator.py` 自动生成于 {os.environ.get("DANA_TODAY", "今日")}')
    lines.append('')
    return '\n'.join(lines)


def get_index_target_file(section_name):
    """根据板块确定要更新的 MOC 文件"""
    targets = {
        '人物志': '索引/人物索引.md',
        '富豪榜': '索引/富豪榜索引.md',
        '心法与原则': '索引/心法索引.md',
        '成长路径': '索引/路径索引.md',
        '方法论与框架': '索引/全部笔记.md',
        '案例研究': '索引/全部笔记.md',
        '资源库': '索引/全部笔记.md',
        'AI研究案例库': '索引/全部笔记.md',
    }
    return ROOT / targets.get(section_name, '索引/全部笔记.md')


def update_index_file(target_path, section_name, items, dry_run=False):
    """更新 MOC 文件，在 AUTO-INDEX 标记之间插入新区块"""
    if not target_path.exists():
        print(f'⚠️  目标文件不存在: {target_path}')
        return False

    content = target_path.read_text(encoding='utf-8')

    new_block = render_index_block(section_name, items)

    if AUTO_START in content and AUTO_END in content:
        # 在标记之间替换
        pattern = re.compile(
            rf'{re.escape(AUTO_START)}.*?{re.escape(AUTO_END)}',
            re.DOTALL
        )
        replacement = f'{AUTO_START}\n\n{new_block}\n{AUTO_END}'
        new_content = pattern.sub(replacement, content, count=1)
    else:
        # 在文件末尾追加新区块
        new_content = content.rstrip() + '\n\n---\n\n' + new_block

    if dry_run:
        print(f'📋 [预览] {target_path.relative_to(ROOT)} (+{len(items)} 条 {section_name})')
        # 仅显示前 5 条
        for line in new_block.split('\n')[:8]:
            print(f'    {line}')
        if len(items) > 5:
            print(f'    ... 还有 {len(items) - 5} 条')
    else:
        target_path.write_text(new_content, encoding='utf-8')
        print(f'✅ 已更新 {target_path.relative_to(ROOT)} (+{len(items)} 条 {section_name})')

    return True


def scan_mode():
    """预览模式：扫描但不写入"""
    print('=' * 60)
    print('📋 索引自动生成预览（不写入文件）')
    print('=' * 60)

    total = 0
    for section_name, _, _ in SECTIONS:
        items = collect_section_items(section_name)
        if not items:
            continue
        target = get_index_target_file(section_name)
        update_index_file(target, section_name, items, dry_run=True)
        total += len(items)

    print(f'\n📊 总计将索引 {total} 条内容到 MOC 文件')


# ============ docs/index.md 统计数字同步 ============

DOCS_INDEX = ROOT / 'docs' / 'index.md'

# docs/index.md 的 stat-label → (计数范围, 数字格式化)
STAT_RULES = [
    ('技术大拿档案', ('人物志',), lambda n: f'{n}+'),
    ('核心心法', ('心法与原则',), lambda n: str(n)),
    ('方法论框架', ('方法论与框架',), lambda n: str(n)),
    ('富豪榜档案', ('WEALTH',), lambda n: f'{n}+'),
    ('研究复盘案例', ('案例研究',), lambda n: str(n)),
]


def compute_section_counts():
    """从真实文件计算各板块数量（富豪榜仅计人物档案）"""
    from audit import is_wealth_person_file
    md_files = find_md_files()
    counts = defaultdict(int)
    for rel in md_files:
        if not rel.parts:
            continue
        counts[rel.parts[0]] += 1
        if rel.name == 'README.md':
            counts[rel.parts[0]] -= 1  # 板块内 README 不计为内容
        if is_wealth_person_file(rel):
            counts['WEALTH'] += 1
    return counts


def sync_docs_stats(dry_run=False):
    """用真实文件数同步 docs/index.md 的统计卡片数字"""
    if not DOCS_INDEX.exists():
        print(f'⚠️  目标文件不存在: {DOCS_INDEX}')
        return

    content = DOCS_INDEX.read_text(encoding='utf-8')
    counts = compute_section_counts()
    changed = 0

    print('=' * 60)
    print('🔄 同步 docs/index.md 统计数字')
    print('=' * 60)

    for label, scope, fmt in STAT_RULES:
        if scope[0] == 'WEALTH':
            n = counts.get('WEALTH', 0)
        else:
            n = counts.get(scope[0], 0)
        new_num = fmt(n)
        pattern = re.compile(
            rf'(<span class="stat-number">)[^<]*(</span>\s*<span class="stat-label">{re.escape(label)})'
        )
        m = pattern.search(content)
        old_num = m.group(0).split('>')[1].split('<')[0] if m else '?'
        if m and old_num != new_num:
            content = pattern.sub(rf'\g<1>{new_num}\g<2>', content, count=1)
            print(f'   {label}: {old_num} → {new_num}')
            changed += 1
        else:
            print(f'   {label}: {new_num}（已是最新）')

    if changed == 0:
        print('\n✅ docs/index.md 数字均已最新，无需修改')
        return

    if dry_run:
        print(f'\n📋 [预览] 将修改 {changed} 处（未写入）')
    else:
        DOCS_INDEX.write_text(content, encoding='utf-8')
        print(f'\n✅ 已更新 docs/index.md（{changed} 处）')


def main():
    args = sys.argv[1:]
    if not args or '--help' in args or '-h' in args:
        print(__doc__)
        sys.exit(0)

    if '--scan' in args:
        scan_mode()
        return

    if '--sync-docs' in args:
        sync_docs_stats(dry_run='--dry-run' in args)
        return

    target_filter = None
    if '--target' in args:
        idx = args.index('--target')
        target_filter = args[idx + 1] if idx + 1 < len(args) else None

    section_filter = None
    if '--section' in args:
        idx = args.index('--section')
        section_filter = args[idx + 1] if idx + 1 < len(args) else None

    print('=' * 60)
    print('🔨 索引自动生成')
    print('=' * 60)

    updated_files = set()
    total_items = 0

    for section_name, _, _ in SECTIONS:
        if section_filter and section_name != section_filter:
            continue
        items = collect_section_items(section_name)
        if not items:
            continue
        target = get_index_target_file(section_name)
        if target_filter and str(target.relative_to(ROOT)) != target_filter:
            continue
        if update_index_file(target, section_name, items):
            updated_files.add(target)
            total_items += len(items)

    print(f'\n📊 更新了 {len(updated_files)} 个文件，共索引 {total_items} 条内容')


if __name__ == '__main__':
    main()