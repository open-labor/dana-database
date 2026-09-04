#!/usr/bin/env python3
"""
dana 知识库审计工具
功能：
  --check-links       扫描所有 Markdown 文件中的 wikilinks，检测 broken links
  --check-frontmatter 检查 frontmatter 完整性和一致性（含富豪榜专用检查）
  --check-wealth      富豪榜数据表完整性 + 关键字段检查
  --check-freshness   检查内容新鲜度（>180 天未更新标记）
  --stats             输出知识库统计信息（按 7 大板块细分）
  --all               执行全部检查

用法：
  python tools/audit.py --all
  python tools/audit.py --check-links
  python tools/audit.py --check-frontmatter
  python tools/audit.py --check-wealth
  python tools/audit.py --check-freshness
  python tools/audit.py --stats
"""

import os
import re
import sys
import yaml
from pathlib import Path
from datetime import datetime, date
from collections import defaultdict

# 项目根目录（脚本位于 tools/，向上退一级）
ROOT = Path(__file__).resolve().parent.parent
# 忽略的路径
IGNORE_DIRS = {'.git', 'tools', 'docs', '.github', '模板', 'GTM'}
# 忽略的根目录文件（项目级文档，非知识库内容）
IGNORE_ROOT_FILES = {'README.md', 'CONTRIBUTING.md', '项目评估与改进报告.md', '更新日志.md'}

# 富豪榜板块标识
WEALTH_DIRS = {'富豪榜'}
# 富豪榜人物档案 frontmatter 必填字段（比通用规则多 4 个）
WEALTH_REQUIRED = {'title', 'tags', 'date', 'born', 'nationality', 'industry', 'company'}
WEALTH_RECOMMENDED = {'peak_rank', 'peak_net_worth', 'first_appeared', 'aliases', 'related'}
# 通用档案必填字段
COMMON_REQUIRED = {'title', 'tags', 'date'}
COMMON_RECOMMENDED = {'aliases', 'related'}
# 内容新鲜度阈值（天）
FRESHNESS_THRESHOLD_DAYS = 180
# 当前日期（用于新鲜度判断，可被环境变量覆盖便于测试）
TODAY = date.today()
try:
    from datetime import date as _date_cls
    TODAY = _date_cls.fromisoformat(os.environ.get('DANA_TODAY', _date_cls.today().isoformat()))
except Exception:
    pass


def find_md_files():
    """递归查找所有 Markdown 文件（排除忽略目录和根目录项目文件）"""
    md_files = []
    for path in ROOT.rglob('*.md'):
        # 检查是否在忽略目录中
        rel = path.relative_to(ROOT)
        if any(part in IGNORE_DIRS for part in rel.parts[:-1]):
            continue
        # 跳过根目录下的项目级文档
        if len(rel.parts) == 1 and rel.name in IGNORE_ROOT_FILES:
            continue
        md_files.append(rel)
    return sorted(md_files)


def find_canvas_files():
    """查找所有 Canvas 文件"""
    return sorted(ROOT.rglob('*.canvas'))


def extract_wikilinks(content):
    """提取 [[...]] 格式的 wikilinks，排除代码块和行内代码中的内容"""
    # 步骤1：临时替换代码块（```...```）
    code_blocks = re.findall(r'```[\s\S]*?```', content)
    for i, block in enumerate(code_blocks):
        content = content.replace(block, f'__CODE_BLOCK_{i}__', 1)

    # 步骤2：临时替换行内代码（`...`）
    inline_codes = re.findall(r'`[^`]+`', content)
    for i, code in enumerate(inline_codes):
        content = content.replace(code, f'__INLINE_CODE_{i}__', 1)

    # 步骤3：提取 wikilinks
    # 允许 \| 作为转义（link 名中包含 |）
    pattern = r'\[\[((?:[^\[\]\\]|\\.)+)\]\]'
    matches = re.findall(pattern, content)
    results = []
    for raw_link in matches:
        # 处理 \| 转义
        unescaped = raw_link.replace('\\|', '|')
        # 分割 link 和 display（首次出现的非转义 |）
        if '|' in unescaped:
            # 找到第一个未转义的 |
            parts = []
            buf = ''
            i = 0
            while i < len(unescaped):
                ch = unescaped[i]
                if ch == '|':
                    parts.append(buf)
                    buf = ''
                else:
                    buf += ch
                i += 1
            parts.append(buf)
            link_text = parts[0].strip() if parts else ''
            display_text = parts[1].strip() if len(parts) > 1 else link_text
        else:
            link_text = unescaped.strip()
            display_text = link_text
        # 去掉 anchor
        link_text = link_text.split('#')[0].strip()
        # 跳过模板占位符（如 [[{{industry}}行业富豪共性]]）
        if '{{' in link_text:
            continue
        results.append((link_text, display_text))
    return results


def is_wealth_file(rel_path):
    """判断文件是否属于富豪榜板块"""
    return bool(rel_path.parts) and rel_path.parts[0] in WEALTH_DIRS


def is_wealth_person_file(rel_path):
    """判断文件是否是富豪榜人物档案（排除索引、模板、综合分析）"""
    if not is_wealth_file(rel_path):
        return False
    rel_str = str(rel_path)
    # 排除：模板、索引文件
    if rel_path.name.startswith('_'):
        return False
    # 排除：综合分析板块
    if rel_str.startswith('富豪榜/综合分析/'):
        return False
    return True


def parse_frontmatter(content):
    """安全解析 YAML frontmatter，返回 dict 或 None"""
    if not content.startswith('---'):
        return None
    try:
        _, fm_str, _ = content.split('---', 2)
        return yaml.safe_load(fm_str) or {}
    except Exception:
        return None


def get_existing_pages(md_files):
    """获取所有存在的页面名（基于文件名、frontmatter aliases 和 Canvas 文件）"""
    existing = set()
    alias_map = {}  # alias -> source file

    # Markdown 文件
    for rel_path in md_files:
        full_path = ROOT / rel_path
        # 文件名（不含扩展名）作为页面名
        page_name = rel_path.stem
        existing.add(page_name)

        # 也添加 aliases
        content = full_path.read_text(encoding='utf-8')
        fm = parse_frontmatter(content)
        if fm and 'aliases' in fm:
            aliases = fm['aliases']
            if isinstance(aliases, list):
                for alias in aliases:
                    existing.add(alias)
                    alias_map[alias] = str(rel_path)

    # Canvas 文件（Obsidian 中 wikilink 可链接到 .canvas）
    for canvas_path in ROOT.rglob('*.canvas'):
        rel = canvas_path.relative_to(ROOT)
        if any(part in IGNORE_DIRS for part in rel.parts[:-1]):
            continue
        # 支持 [[文件名]] 和 [[目录/文件名]] 两种形式
        existing.add(rel.stem)
        existing.add(str(rel.with_suffix('')))

    return existing, alias_map


def get_all_page_targets():
    """所有可被 [[...]] 引用的页面名（含被忽略为"源文件"的项目级文档，如 README、更新日志、评估报告）

    源文件扫描范围（find_md_files）与链接目标范围是两回事：
    一个文件可以不被审计其内容，但仍应是合法的链接目标。
    """
    targets = set()
    for path in ROOT.rglob('*.md'):
        rel = path.relative_to(ROOT)
        targets.add(rel.stem)
        targets.add(str(rel.with_suffix('')))
    for canvas_path in ROOT.rglob('*.canvas'):
        rel = canvas_path.relative_to(ROOT)
        targets.add(rel.stem)
        targets.add(str(rel.with_suffix('')))
    return targets


def check_links(md_files, existing_pages):
    """检查 broken wikilinks"""
    print("=" * 60)
    print("🔍 检查 Wikilink 健康度")
    print("=" * 60)

    # 扩展 existing_pages 以支持带路径前缀的 wikilinks
    # 例：[[富豪榜/福布斯全球/Jeff Bezos]] 应能匹配文件 "富豪榜/福布斯全球/Jeff Bezos.md"
    expanded = set()
    for rel in md_files:
        rel_str = str(rel)
        if rel_str.endswith('.md'):
            expanded.add(rel_str[:-3])
        expanded.add(rel.stem)

    broken = []
    total_links = 0
    link_counts = defaultdict(int)
    file_link_counts = defaultdict(int)

    for rel_path in md_files:
        full_path = ROOT / rel_path
        content = full_path.read_text(encoding='utf-8')
        links = extract_wikilinks(content)

        for link_text, display_text in links:
            total_links += 1
            link_counts[link_text] += 1
            file_link_counts[str(rel_path)] += 1

            # 三种匹配尝试
            if link_text in existing_pages or link_text in expanded:
                continue
            # 尝试去目录前缀后匹配（兼容 [[目录/文件名]] 和 [[文件名]] 互链）
            if '/' in link_text:
                tail = link_text.split('/')[-1]
                if tail in existing_pages:
                    continue
            broken.append({
                'file': str(rel_path),
                'link': link_text,
                'display': display_text,
                'line': None
            })

    print(f"\n📊 统计：")
    print(f"   Markdown 文件数: {len(md_files)}")
    print(f"   Wikilink 总数: {total_links}")
    print(f"   唯一链接目标数: {len(link_counts)}")
    print(f"   平均每文件链接数: {total_links / len(md_files):.1f}" if md_files else "   N/A")

    if broken:
        print(f"\n❌ 发现 {len(broken)} 个 broken links：")
        by_file = defaultdict(list)
        for b in broken:
            by_file[b['file']].append(b)

        for file, items in sorted(by_file.items()):
            print(f"\n   📄 {file}")
            for item in items:
                print(f"      → [[{item['link']}]]")
    else:
        print("\n✅ 所有 wikilinks 均有效！")

    print(f"\n📈 最常链接的目标（Top 20）：")
    for target, count in sorted(link_counts.items(), key=lambda x: -x[1])[:20]:
        status = "✅" if target in existing_pages else "❌"
        print(f"   {status} {target} ({count} 次)")

    return broken


def check_frontmatter(md_files):
    """检查 frontmatter 完整性（区分富豪档 vs 普通档案）"""
    print("\n" + "=" * 60)
    print("🔍 检查 Frontmatter 完整性")
    print("=" * 60)

    issues = []
    stats = {
        'with_frontmatter': 0,
        'without_frontmatter': 0,
        'wealth_with_full': 0,
        'wealth_total': 0,
        'field_coverage': defaultdict(int),
    }

    for rel_path in md_files:
        full_path = ROOT / rel_path
        content = full_path.read_text(encoding='utf-8')

        if not content.startswith('---'):
            stats['without_frontmatter'] += 1
            issues.append({
                'file': str(rel_path),
                'type': '缺少 frontmatter',
                'detail': '文件开头没有 YAML frontmatter'
            })
            continue

        stats['with_frontmatter'] += 1
        frontmatter = parse_frontmatter(content)
        if frontmatter is None:
            issues.append({
                'file': str(rel_path),
                'type': 'frontmatter 解析错误',
                'detail': 'YAML 解析失败'
            })
            continue

        # 根据板块选择必填字段
        if is_wealth_person_file(rel_path):
            required = WEALTH_REQUIRED
            recommended = WEALTH_RECOMMENDED
            stats['wealth_total'] += 1
            if all(f in frontmatter for f in required):
                stats['wealth_with_full'] += 1
        else:
            required = COMMON_REQUIRED
            recommended = COMMON_RECOMMENDED

        for field in required:
            if field not in frontmatter:
                issues.append({
                    'file': str(rel_path),
                    'type': f'缺少必填字段: {field}',
                    'detail': f'frontmatter 中未找到 {field}'
                })
            else:
                stats['field_coverage'][field] += 1

        for field in recommended:
            if field in frontmatter:
                stats['field_coverage'][field] += 1

        # date 键存在但值非法时，新鲜度监控会静默失效——必须显式校验
        # （模板文件的 {{date}} 占位符豁免）
        date_val = frontmatter.get('date')
        date_ok = False
        if isinstance(date_val, str) and '{{' in date_val:
            date_ok = True
        elif isinstance(date_val, (datetime, date)):
            date_ok = True
        elif isinstance(date_val, str):
            try:
                date.fromisoformat(date_val)
                date_ok = True
            except ValueError:
                date_ok = False
        if date_val is not None and not date_ok:
            issues.append({
                'file': str(rel_path),
                'type': 'date 值格式错误',
                'detail': f'date={date_val!r} 不是 YYYY-MM-DD'
            })

    total = len(md_files)
    print(f"\n📊 统计：")
    print(f"   含 frontmatter: {stats['with_frontmatter']} / {total}")
    print(f"   不含 frontmatter: {stats['without_frontmatter']} / {total}")
    if stats['wealth_total']:
        wealth_pct = stats['wealth_with_full'] / stats['wealth_total'] * 100
        print(f"   富豪档完整字段覆盖率: {stats['wealth_with_full']} / {stats['wealth_total']} ({wealth_pct:.0f}%)")

    print(f"\n📈 必填字段覆盖率：")
    all_required = COMMON_REQUIRED | WEALTH_REQUIRED
    for field in sorted(all_required):
        count = stats['field_coverage'][field]
        pct = count / total * 100 if total else 0
        print(f"   {field}: {count}/{total} ({pct:.0f}%)")

    print(f"\n📈 推荐字段覆盖率：")
    for field in sorted(COMMON_RECOMMENDED | WEALTH_RECOMMENDED):
        count = stats['field_coverage'][field]
        pct = count / total * 100 if total else 0
        print(f"   {field}: {count}/{total} ({pct:.0f}%)")

    if issues:
        print(f"\n❌ 发现 {len(issues)} 个问题：")
        by_type = defaultdict(list)
        for issue in issues:
            by_type[issue['type']].append(issue)

        for issue_type, items in sorted(by_type.items()):
            print(f"\n   🔴 {issue_type} ({len(items)} 个文件)")
            for item in items[:5]:
                print(f"      → {item['file']}")
            if len(items) > 5:
                print(f"      ... 还有 {len(items) - 5} 个")
    else:
        print("\n✅ 所有 frontmatter 均完整！")

    return issues


def check_wealth(md_files):
    """富豪榜专用检查：数据表完整性 + 关键章节存在"""
    print("\n" + "=" * 60)
    print("💰 检查富豪榜数据完整性")
    print("=" * 60)

    wealth_files = [p for p in md_files if is_wealth_file(p)]
    issues = []
    stats = {
        '人物档案': 0,
        '总览/索引': 0,
        '综合分析': 0,
        'with_table': 0,
        'with_sources': 0,
        'with_principles': 0,
    }

    # 关键章节标识（富豪档模板要求）
    REQUIRED_SECTIONS = {
        '数据表': re.compile(r'##\s+四、财富轨迹'),
        '来源': re.compile(r'##\s+参考来源'),
        '心法': re.compile(r'##\s+[六七]、\s*可复用'),
    }

    for rel_path in wealth_files:
        content = (ROOT / rel_path).read_text(encoding='utf-8')

        # 板块分类
        rel_str = str(rel_path)
        if rel_str.endswith('_总览.md') or rel_str.endswith('_主索引.md') or rel_str.endswith('_交叉对照索引.md'):
            stats['总览/索引'] += 1
            continue
        # 群像/多人物对比文件本质是综合分析，不适用个人档案章节模板
        if '群像' in rel_path.name or '/综合分析/' in rel_str:
            stats['综合分析'] += 1
            continue

        stats['人物档案'] += 1

        # 检查关键章节
        has_table = bool(REQUIRED_SECTIONS['数据表'].search(content))
        has_sources = bool(REQUIRED_SECTIONS['来源'].search(content))
        has_principles = bool(REQUIRED_SECTIONS['心法'].search(content))

        if has_table:
            stats['with_table'] += 1
        else:
            # 部分档案（如 Elon Musk 补充档）允许省略数据表
            if not re.search(r'##\s+一、财富轨迹', content):
                issues.append({
                    'file': str(rel_path),
                    'type': '缺少财富轨迹表',
                    'detail': '未找到 "## 四、财富轨迹" 或 "## 一、财富轨迹" 章节'
                })

        if has_sources:
            stats['with_sources'] += 1
        else:
            issues.append({
                'file': str(rel_path),
                'type': '缺少参考来源章节',
                'detail': '未找到 "## 参考来源" 章节'
            })

        if has_principles:
            stats['with_principles'] += 1
        else:
            issues.append({
                'file': str(rel_path),
                'type': '缺少心法提炼章节',
                'detail': '未找到 "## 七、可复用" 章节'
            })

    print(f"\n📊 富豪榜板块构成：")
    print(f"   人物档案: {stats['人物档案']}")
    print(f"   总览/索引: {stats['总览/索引']}")
    print(f"   综合分析: {stats['综合分析']}")
    print(f"   合计: {sum(stats.values())} 个文件")

    if stats['人物档案']:
        print(f"\n📈 关键章节覆盖率（人物档案）：")
        print(f"   含数据表: {stats['with_table']} / {stats['人物档案']} ({stats['with_table']/stats['人物档案']*100:.0f}%)")
        print(f"   含参考来源: {stats['with_sources']} / {stats['人物档案']} ({stats['with_sources']/stats['人物档案']*100:.0f}%)")
        print(f"   含心法提炼: {stats['with_principles']} / {stats['人物档案']} ({stats['with_principles']/stats['人物档案']*100:.0f}%)")

    if issues:
        print(f"\n❌ 发现 {len(issues)} 个问题：")
        by_type = defaultdict(list)
        for issue in issues:
            by_type[issue['type']].append(issue)
        for issue_type, items in sorted(by_type.items()):
            print(f"\n   🔴 {issue_type} ({len(items)} 个文件)")
            for item in items[:5]:
                print(f"      → {item['file']}")
            if len(items) > 5:
                print(f"      ... 还有 {len(items) - 5} 个")
    else:
        print("\n✅ 所有富豪档关键章节完整！")

    return issues


def check_freshness(md_files):
    """检查内容新鲜度：基于 frontmatter date 字段检测 >180 天未更新"""
    print("\n" + "=" * 60)
    print(f"🕐 检查内容新鲜度（阈值 {FRESHNESS_THRESHOLD_DAYS} 天）")
    print("=" * 60)

    stale = []
    fresh = []
    no_date = []
    parse_errors = []
    no_frontmatter = []

    for rel_path in md_files:
        # 下划线前缀文件是索引/模板/数据等元文件，不参与内容新鲜度
        if rel_path.name.startswith('_'):
            continue
        full_path = ROOT / rel_path
        content = full_path.read_text(encoding='utf-8')
        if not content.startswith('---'):
            no_frontmatter.append(str(rel_path))
            continue
        fm = parse_frontmatter(content)
        if fm is None:
            parse_errors.append(str(rel_path))
            continue
        if 'date' not in fm:
            no_date.append(str(rel_path))
            continue

        file_date = fm['date']
        # 支持 date 或 datetime 类型
        if isinstance(file_date, datetime):
            file_date = file_date.date()
        elif isinstance(file_date, str):
            try:
                file_date = date.fromisoformat(file_date)
            except ValueError:
                no_date.append(f"{rel_path} (date 格式错误)")
                continue
        elif not isinstance(file_date, date):
            no_date.append(f"{rel_path} (date 类型不支持)")
            continue

        age = (TODAY - file_date).days
        if age > FRESHNESS_THRESHOLD_DAYS:
            stale.append({
                'file': str(rel_path),
                'date': file_date.isoformat(),
                'age_days': age
            })
        else:
            fresh.append(rel_path)

    print(f"\n📊 统计：")
    print(f"   当前日期: {TODAY.isoformat()}")
    print(f"   阈值: {FRESHNESS_THRESHOLD_DAYS} 天")
    print(f"   新鲜: {len(fresh)} 篇")
    print(f"   陈旧: {len(stale)} 篇")
    if parse_errors:
        print(f"   ⚠️ frontmatter 解析失败: {len(parse_errors)} 篇（单独修复，不计入无 date）")
        for p in parse_errors:
            print(f"      → {p}")
    if no_frontmatter:
        print(f"   无 frontmatter: {len(no_frontmatter)} 篇")
    if no_date:
        print(f"   无 date 字段: {len(no_date)} 篇")

    if stale:
        # 按陈旧程度排序
        stale.sort(key=lambda x: -x['age_days'])
        print(f"\n⚠️  陈旧内容 Top 10（建议季度回顾）：")
        for item in stale[:10]:
            months = item['age_days'] // 30
            print(f"   📄 {item['file']} ({item['date']}, {months} 个月前)")
        if len(stale) > 10:
            print(f"   ... 还有 {len(stale) - 10} 篇")

    return stale


def print_stats(md_files):
    """输出知识库整体统计（按 7 大板块细分）"""
    print("\n" + "=" * 60)
    print("📊 知识库整体统计")
    print("=" * 60)

    canvas_files = find_canvas_files()
    print(f"\n📁 文件统计：")
    print(f"   Markdown 文件: {len(md_files)}")
    print(f"   Canvas 文件: {len(canvas_files)}")

    # 按板块统计（首层目录归类）
    SECTIONS = {
        '人物志': '人物志',
        '富豪榜': '富豪榜',
        '心法与原则': '心法与原则',
        '成长路径': '成长路径',
        '方法论与框架': '方法论与框架',
        '案例研究': '案例研究',
        '资源库': '资源库',
        '索引': '索引',
    }

    section_counts = defaultdict(int)
    section_chars = defaultdict(int)

    for rel_path in md_files:
        top = rel_path.parts[0] if rel_path.parts else '根目录'
        section_counts[top] += 1
        content = (ROOT / rel_path).read_text(encoding='utf-8')
        section_chars[top] += len(content)

    print(f"\n📂 按板块分布：")
    for section_name in SECTIONS:
        count = section_counts.get(section_name, 0)
        chars = section_chars.get(section_name, 0)
        if count:
            avg = chars // count
            print(f"   {section_name}: {count} 篇 (~{chars:,} 字符, 平均 {avg:,})")

    # 其他（AI研究案例库.md 等根目录文件）
    other = [(k, v) for k, v in section_counts.items() if k not in SECTIONS]
    if other:
        print(f"\n📄 根目录内容：")
        for name, count in sorted(other):
            chars = section_chars[name]
            print(f"   {name}: {count} 篇 (~{chars:,} 字符)")

    total_chars = sum(section_chars.values())
    print(f"\n📝 内容规模：")
    print(f"   总字符数: ~{total_chars:,}")
    print(f"   平均每文件: ~{total_chars // len(md_files):,} 字符" if md_files else "   N/A")


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    run_all = '--all' in args
    check_links_flag = run_all or '--check-links' in args
    check_frontmatter_flag = run_all or '--check-frontmatter' in args
    check_wealth_flag = run_all or '--check-wealth' in args
    check_freshness_flag = run_all or '--check-freshness' in args
    stats_flag = run_all or '--stats' in args

    md_files = find_md_files()
    existing_pages, alias_map = get_existing_pages(md_files)
    # 链接目标集合 = 被扫描文件 + 所有存在的 md/canvas（含项目级文档）
    link_targets = existing_pages | get_all_page_targets()

    print(f"🚀 dana 知识库审计工具")
    print(f"   项目根目录: {ROOT}")
    print(f"   扫描 Markdown 文件: {len(md_files)} 个")

    if stats_flag or run_all:
        print_stats(md_files)

    if check_links_flag:
        check_links(md_files, link_targets)

    if check_frontmatter_flag:
        check_frontmatter(md_files)

    if check_wealth_flag:
        check_wealth(md_files)

    if check_freshness_flag:
        check_freshness(md_files)

    print("\n" + "=" * 60)
    print("✅ 审计完成")
    print("=" * 60)


if __name__ == '__main__':
    main()