#!/usr/bin/env python3
"""
GitHub Actions 脚本：用 Playwright 抓取东方财富行业资金流向数据
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

# 多对一映射的行业，net 值需要合并
MANY_TO_ONE = ["军工装备", "文化传媒", "汽车整车", "建筑装饰", "养殖业"]


def parse_value(raw: str) -> float:
    """将 '29.81亿' / '1.5%' / '1.2' 转为 float"""
    raw = raw.strip()
    if not raw:
        return 0.0
    if raw.endswith("亿"):
        return float(raw[:-1])
    if raw.endswith("%"):
        return float(raw[:-1])
    return float(raw)


async def intercept_api(page, fid, fields):
    """拦截并获取某个周期的 API 数据"""
    url_pattern = f"fid={fid}"
    fields_list = fields.split(",")

    request_promise = None

    async def handle_request(request):
        nonlocal request_promise
        if url_pattern in request.url and request_promise is None:
            try:
                response = await request.response()
                body = await response.body()
                data = json.loads(body)
                if data.get("data") and data["data"].get("diff"):
                    request_promise = []
                    for item in data["data"]["diff"]:
                        entry = {}
                        for i, f in enumerate(fields_list):
                            val = item.get(f)
                            if val == "-" or val is None:
                                val = "0"
                            entry[f] = val
                        request_promise.append(entry)
            except Exception:
                pass

    page.on("requestfinished", handle_request)

    # 通过修改 URL 参数的方式触发对应周期请求（实际上页面切换tab时会自动触发）
    # 这里通过 JS 点击 tab 来触发
    tab_map = {
        "f62": "0",   # 今日
        "f267": "1",  # 3日
        "f164": "2",  # 5日
        "f174": "3",  # 10日
    }
    tab_idx = tab_map.get(fid, "0")

    await page.evaluate(f"""
        const tabs = document.querySelectorAll('.tab-item, .tab-item-left, [class*="tab"]');
        for (const t of tabs) {{
            if (t.textContent.includes('今日') || t.textContent.includes('3日') || 
                t.textContent.includes('5日') || t.textContent.includes('10日')) {{
                const idx = {tab_idx};
                if (idx < tabs.length) {{
                    tabs[idx].click();
                }}
                break;
            }}
        }}
    """)

    await page.wait_for_timeout(3000)
    page.remove_all_listeners("requestfinished")

    return request_promise


async def fetch_data():
    """主抓取逻辑"""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
        ])
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        # 收集所有周期的数据
        all_data = {}

        async def collect_period(period_name, fid, fields, tab_js_condition):
            request_promise = []

            async def handle_request(request):
                if f"fid={fid}" in request.url and "fs=m:90+t2" in request.url:
                    try:
                        resp = await request.response()
                        body = await resp.body()
                        data = json.loads(body)
                        if data.get("data") and data["data"].get("diff"):
                            for item in data["data"]["diff"]:
                                entry = {}
                                f12 = item.get("f12", "")
                                f14 = item.get("f14", "")
                                entry["name"] = f14
                                entry["code"] = f12
                                for f in fields.split(","):
                                    if f in ("f12", "f14"):
                                        continue
                                    val = item.get(f, "0")
                                    if val == "-" or val is None:
                                        val = "0"
                                    entry[f] = val
                                request_promise.append(entry)
                    except Exception:
                        pass

            page.on("requestfinished", handle_request)

            # 点击对应 tab
            await page.evaluate(tab_js_condition)
            await page.wait_for_timeout(3000)
            page.remove_all_listeners("requestfinished")
            return request_promise

        # 访问页面
        await page.goto("https://data.eastmoney.com/bkzj/hy.html", wait_until="networkidle", timeout=90000)
        await page.wait_for_timeout(5000)

        # 按顺序抓取4个周期
        for period, fid, fields, tab_js in [
            ("今日", "f62", "f12,f14,f3,f62,f184",
             'document.querySelectorAll("[class*=\'tab\']").forEach(e=>{if(e.textContent.trim()===\'今日\')e.click()})'),
            ("3日", "f267", "f12,f14,f127,f267,f268",
             'document.querySelectorAll("[class*=\'tab\']").forEach(e=>{if(e.textContent.trim()===\'3日\')e.click()})'),
            ("5日", "f164", "f12,f14,f109,f164,f165",
             'document.querySelectorAll("[class*=\'tab\']").forEach(e=>{if(e.textContent.trim()===\'5日\')e.click()})'),
            ("10日", "f174", "f12,f14,f160,f174,f175",
             'document.querySelectorAll("[class*=\'tab\']").forEach(e=>{if(e.textContent.trim()===\'10日\')e.click()})'),
        ]:
            print(f"  抓取 {period} ...")
            items = await collect_period(period, fid, fields, tab_js)
            if not items:
                print(f"    ⚠ 未获取到数据")
            else:
                print(f"    ✓ {len(items)} 条")
            all_data[period] = items

        await browser.close()
    return all_data


def convert_to_ths_format(em_data):
    """
    将东财数据转为同花顺格式
    东财有50个行业，同花顺有88个行业
    未映射到的同花顺行业 net=0
    """
    periods = ["今日", "3日", "5日", "10日"]

    # 聚合：东财行业 → 同花顺行业 (处理多对一)
    aggregated = {p: {} for p in periods}

    net_field_map = {
        "今日": "f62",
        "3日": "f267",
        "5日": "f164",
        "10日": "f174",
    }
    pct_field_map = {
        "今日": "f3",
        "3日": "f127",
        "5日": "f109",
        "10日": "f160",
    }
    rate_field_map = {
        "今日": "f184",
        "3日": "f268",
        "5日": "f165",
        "10日": "f175",
    }

    for period in periods:
        items = em_data.get(period, [])
        for item in items:
            em_name = item.get("name", "")
            ths_name = EM_TO_THS.get(em_name)
            if not ths_name:
                continue

            net_val = parse_value(str(item.get(net_field_map[period], "0")))
            pct_val = parse_value(str(item.get(pct_field_map[period], "0")))

            if ths_name not in aggregated[period]:
                aggregated[period][ths_name] = {"net": 0.0, "tradezdf": 0.0, "count": 0}

            aggregated[period][ths_name]["net"] += net_val
            aggregated[period][ths_name]["tradezdf"] = max(
                aggregated[period][ths_name]["tradezdf"], pct_val
            )
            aggregated[period][ths_name]["count"] += 1

    # 转为数组格式
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

    # 20日：因东财不支持，使用10日数据替代
    result["20日"] = [dict(item) for item in result["10日"]]

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="抓取东财行业资金流向")
    parser.add_argument("-o", "--output", default="./output/industry_fund_flow.js", help="输出文件路径")
    parser.add_argument("--dry-run", action="store_true", help="仅测试，不写入文件")
    args = parser.parse_args()

    print("=" * 50)
    print("东方财富行业资金流向抓取")
    print(f"时间: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 抓取
    print("\n[1/3] 启动 Playwright ...")
    em_data = asyncio.run(fetch_data())

    total = sum(len(v) for v in em_data.values())
    print(f"\n抓取完成，共 {total} 条记录")

    # 转换
    print("\n[2/3] 转换为同花顺格式 ...")
    ths_data = convert_to_ths_format(em_data)

    # 日期
    today = datetime.now(timezone(timedelta(hours=8)))
    date_str = today.strftime("%Y-%m-%d")

    output_obj = {
        **ths_data,
        "DATE": date_str,
    }

    # 生成 JS 文件
    js_content = f"// 行业资金流向数据 - auto-generated by GitHub Actions\nvar IND_FLOW = {json.dumps(output_obj, ensure_ascii=False)};\n"

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

    # 输出摘要
    print("\n" + "=" * 50)
    print("数据摘要:")
    for period in ["今日", "3日", "5日", "10日"]:
        items = sorted(ths_data.get(period, []), key=lambda x: x["net"], reverse=True)
        top3 = items[:3]
        print(f"  {period} Top3: ", end="")
        for item in top3:
            print(f"{item['industry']}({item['net']:.2f}亿) ", end="")
        print()
    print(f"  DATE: {date_str}")
    print("=" * 50)


if __name__ == "__main__":
    main()
