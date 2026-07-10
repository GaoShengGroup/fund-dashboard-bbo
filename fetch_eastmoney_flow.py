#!/usr/bin/env python3
"""
GitHub Actions 脚本：用 Playwright 抓取东方财富行业资金流向数据（全量翻页）
生成 industry_fund_flow.js（兼容现有仪表盘格式）

用法:
  python fetch_eastmoney_flow.py -o ./output/industry_fund_flow.js
"""

import asyncio
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 东财→同花顺行业名映射表
EM_TO_THS = {
    "航天装备Ⅱ": "军工装备", "军工电子Ⅱ": "军工电子", "汽车零部件": "汽车零部件",
    "医疗服务": "医疗服务", "IT服务Ⅱ": "IT服务", "工业金属": "工业金属",
    "化学制药": "化学制药", "电力": "电力", "航海装备Ⅱ": "军工装备",
    "广告营销": "文化传媒", "风电设备": "风电设备", "白酒Ⅱ": "白酒",
    "通用设备": "通用设备", "乘用车": "汽车整车", "通信服务": "通信服务",
    "其他电子Ⅱ": "其他电子", "家电零部件Ⅱ": "白色家电", "银行Ⅱ": "银行",
    "贵金属": "贵金属", "航空装备Ⅱ": "军工装备", "养殖业": "养殖业",
    "游戏Ⅱ": "游戏", "专业工程": "建筑装饰", "金属新材料": "金属新材料",
    "中药Ⅱ": "中药", "普钢": "钢铁", "生物制品": "生物制品",
    "一般零售": "零售", "工程机械": "工程机械", "化学纤维": "化学纤维",
    "地面兵装Ⅱ": "军工装备", "燃气Ⅱ": "燃气", "小金属": "小金属",
    "基础建设": "建筑装饰", "商用车": "汽车整车", "保险Ⅱ": "保险",
    "物流": "物流", "航空机场": "机场航运", "黑色家电": "黑色家电",
    "电视广播Ⅱ": "文化传媒", "非金属材料Ⅱ": "非金属材料",
    "旅游零售Ⅱ": "旅游及酒店", "房地产开发": "房地产",
    "电机Ⅱ": "电机", "家居用品": "家居用品", "饲料": "养殖业",
    "贸易Ⅱ": "贸易", "饮料乳品": "饮料制造", "医疗美容": "美容护理",
    "调味发酵品Ⅱ": "食品加工制造",
}

# API 配置：周期 → (fid, fields)
PERIOD_CONFIG = {
    "今日": ("f62", "f12,f14,f3,f62,f184"),
    "3日": ("f267", "f12,f14,f127,f267,f268"),
    "5日": ("f164", "f12,f14,f109,f164,f165"),
    "10日": ("f174", "f12,f14,f160,f174,f175"),
}


def parse_value(raw):
    """将 '29.81亿' / '1.5%' / '1.2' 转为 float"""
    if isinstance(raw, (int, float)):
        return float(raw)
    raw = str(raw).strip()
    if not raw or raw == "-":
        return 0.0
    if raw.endswith("亿"):
        return float(raw[:-1])
    if raw.endswith("%"):
        return float(raw[:-1])
    return float(raw)


async def fetch_page(page, fid, fields, pn):
    """在浏览器上下文中调用 push2 API，返回单页数据"""
    url = (
        f"https://push2.eastmoney.com/api/qt/clist/get"
        f"?fid={fid}&po=1&pz=50&pn={pn}&np=1&fltt=2&invt=2"
        f"&fs=m:90+t2&fields={fields}"
    )
    result = await page.evaluate(f"""
        async () => {{
            const resp = await fetch(`{url}`);
            return await resp.json();
        }}
    """)
    data = result.get("data")
    if not data or not data.get("diff"):
        return []
    return data["diff"]


async def fetch_all_pages(page, fid, fields):
    """翻页抓取所有行业数据"""
    all_items = []
    for pn in range(1, 10):  # 最多 10 页（128 行业 = 3 页）
        items = await fetch_page(page, fid, fields, pn)
        if not items:
            break
        all_items.extend(items)
        if len(items) < 50:
            break  # 最后一页不足 50 条
    return all_items


async def fetch_data():
    """主抓取逻辑"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
        ])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # 访问页面获取 session cookie
        print("  访问 data.eastmoney.com ...")
        await page.goto("https://data.eastmoney.com/bkzj/hy.html",
                        wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        all_data = {}
        for period, (fid, fields) in PERIOD_CONFIG.items():
            print(f"  抓取 {period} ...", end=" ", flush=True)
            items = await fetch_all_pages(page, fid, fields)
            all_data[period] = items
            print(f"{len(items)} 条")

        await browser.close()
    return all_data


def convert_to_ths_format(em_data):
    """将东财数据转为同花顺格式，处理多对一合并"""
    periods = ["今日", "3日", "5日", "10日"]

    net_field_map = {
        "今日": "f62", "3日": "f267", "5日": "f164", "10日": "f174",
    }
    pct_field_map = {
        "今日": "f3", "3日": "f127", "5日": "f109", "10日": "f160",
    }

    aggregated = {p: {} for p in periods}

    for period in periods:
        for item in em_data.get(period, []):
            em_name = item.get("f14", "")
            ths_name = EM_TO_THS.get(em_name)
            if not ths_name:
                continue

            net_val = parse_value(item.get(net_field_map[period], 0))
            pct_val = parse_value(item.get(pct_field_map[period], 0))

            if ths_name not in aggregated[period]:
                aggregated[period][ths_name] = {"net": 0.0, "tradezdf": 0.0, "count": 0}

            aggregated[period][ths_name]["net"] += net_val
            aggregated[period][ths_name]["tradezdf"] = max(
                aggregated[period][ths_name]["tradezdf"], pct_val
            )
            aggregated[period][ths_name]["count"] += 1

    result = {}
    for period in periods:
        result[period] = [
            {
                "industry": name,
                "net": round(data["net"], 2),
                "tradezdf": round(data["tradezdf"], 2),
                "flowIn": None,
                "flowOut": None,
            }
            for name, data in aggregated[period].items()
        ]

    # 20日：东财不支持，用10日替代
    result["20日"] = [dict(item) for item in result["10日"]]

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="抓取东财行业资金流向（全量翻页）")
    parser.add_argument("-o", "--output", default="./output/industry_fund_flow.js", help="输出文件路径")
    parser.add_argument("--dry-run", action="store_true", help="仅测试，不写入文件")
    args = parser.parse_args()

    print("=" * 50)
    print("东方财富行业资金流向抓取（全量翻页）")
    print(f"时间: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    print("\n[1/3] 启动 Playwright 并翻页抓取 ...")
    em_data = asyncio.run(fetch_data())

    total = sum(len(v) for v in em_data.values())
    print(f"\n抓取完成，共 {total} 条记录")

    print("\n[2/3] 转换为同花顺格式 ...")
    ths_data = convert_to_ths_format(em_data)

    today = datetime.now(timezone(timedelta(hours=8)))
    date_str = today.strftime("%Y-%m-%d")

    output_obj = {**ths_data, "DATE": date_str}
    js_content = (
        "// 行业资金流向数据 - auto-generated by GitHub Actions\n"
        f"var IND_FLOW = {json.dumps(output_obj, ensure_ascii=False)};\n"
    )

    if args.dry_run:
        print(f"\n[3/3] DRY RUN: 未写入文件")
        print(f"数据大小: {len(js_content)} 字节")
        print(f"行业数: {len(ths_data.get('今日', []))}")
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(js_content, encoding="utf-8")
        print(f"\n[3/3] 已写入: {output_path}")
        print(f"文件大小: {output_path.stat().st_size} 字节")
        print(f"行业数: {len(ths_data.get('今日', []))}")

    # 摘要
    print("\n" + "=" * 50)
    print("数据摘要:")
    for period in ["今日", "3日", "5日", "10日"]:
        items = sorted(ths_data.get(period, []), key=lambda x: x["net"], reverse=True)
        top3_in = items[:3]
        bottom3_out = sorted(items, key=lambda x: x["net"])[:3]
        print(f"  {period}:")
        print(f"    流入 Top3: " + "  ".join(f"{x['industry']}({x['net']:.2f}亿)" for x in top3_in))
        print(f"    流出 Top3: " + "  ".join(f"{x['industry']}({x['net']:.2f}亿)" for x in bottom3_out))
    print(f"  DATE: {date_str}")
    print("=" * 50)


if __name__ == "__main__":
    main()
