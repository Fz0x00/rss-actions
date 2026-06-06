#!/usr/bin/env python3
"""
Daily Report Generator - 生成 GitHub Actions 运行日报并发送到飞书

功能:
  - 获取过去 24 小时的所有 workflow runs
  - 统计每个 workflow 的运行次数、成功/失败率
  - 统计触发类型 (schedule/workflow_dispatch/repository_dispatch)
  - 发送到飞书 webhook
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone


def get_workflow_runs(repo_owner, repo_name, since_time):
    """通过 gh CLI 获取指定时间之后的所有 workflow runs"""
    cmd = [
        "gh", "run", "list",
        "--repo", f"{repo_owner}/{repo_name}",
        "--limit", "200",
        "--json", "name,status,conclusion,createdAt,event,databaseId",
        "--jq", f'.[] | select(.createdAt >= "{since_time}")'
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ})
    if result.returncode != 0:
        print(f"Error fetching runs: {result.stderr}", file=sys.stderr)
        return []
    
    runs = []
    for line in result.stdout.strip().split('\n'):
        if line:
            runs.append(json.loads(line))
    return runs


def get_run_jobs(run_id, repo_owner, repo_name):
    """获取单个 run 的 job 详情"""
    cmd = [
        "gh", "run", "view", str(run_id),
        "--repo", f"{repo_owner}/{repo_name}",
        "--json", "jobs"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, env={**os.environ})
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
        return data.get("jobs", [])
    except:
        return []


def extract_articles_count(jobs):
    """从 job 日志中提取文章数量"""
    for job in jobs:
        steps = job.get("steps", [])
        for step in steps:
            name = step.get("name", "")
            if "fetch" in name.lower() or "summarize" in name.lower():
                # 这里可以进一步解析日志获取具体数量
                pass
    return None


def format_report(runs, repo_owner, repo_name):
    """格式化报告"""
    if not runs:
        return "📊 过去 24 小时没有 workflow 运行记录"
    
    # 按 workflow 分组，并记录失败的 run
    workflows = {}
    failed_runs = []
    
    for run in runs:
        name = run.get("name", "Unknown")
        if name not in workflows:
            workflows[name] = {
                "total": 0,
                "success": 0,
                "failure": 0,
                "in_progress": 0,
                "triggers": {}
            }
        
        wf = workflows[name]
        wf["total"] += 1
        
        status = run.get("status", "")
        conclusion = run.get("conclusion", "")
        event = run.get("event", "unknown")
        run_id = run.get("databaseId", "")
        created_at = run.get("createdAt", "")
        
        if status == "in_progress":
            wf["in_progress"] += 1
        elif conclusion == "success":
            wf["success"] += 1
        elif conclusion == "failure":
            wf["failure"] += 1
            # 记录失败详情
            failed_runs.append({
                "name": name,
                "id": run_id,
                "time": created_at,
                "event": event
            })
        
        wf["triggers"][event] = wf["triggers"].get(event, 0) + 1
    
    # 生成报告
    report_lines = ["📊 RSS Monitor 日报", f"时间范围: 过去 24 小时", ""]
    
    for wf_name, stats in workflows.items():
        report_lines.append(f"🔄 {wf_name}")
        report_lines.append(f"  总次数: {stats['total']}")
        report_lines.append(f"  ✅ 成功: {stats['success']}")
        if stats['failure'] > 0:
            report_lines.append(f"  ❌ 失败: {stats['failure']}")
        if stats['in_progress'] > 0:
            report_lines.append(f"  ⏳ 进行中: {stats['in_progress']}")
        
        # 触发类型
        triggers = stats['triggers']
        trigger_str = ", ".join([f"{k}: {v}" for k, v in triggers.items()])
        report_lines.append(f"  触发: {trigger_str}")
        report_lines.append("")
    
    # 失败记录详情
    if failed_runs:
        report_lines.append("❌ 失败记录:")
        for fr in failed_runs:
            time_short = fr['time'][:16].replace('T', ' ') if fr['time'] else 'N/A'
            run_url = f"https://github.com/{repo_owner}/{repo_name}/actions/runs/{fr['id']}"
            report_lines.append(f"  • {fr['name']} ({fr['event']})")
            report_lines.append(f"    时间: {time_short}")
            report_lines.append(f"    链接: {run_url}")
        report_lines.append("")
    
    # 总计
    total_runs = sum(wf["total"] for wf in workflows.values())
    total_success = sum(wf["success"] for wf in workflows.values())
    total_failure = sum(wf["failure"] for wf in workflows.values())
    
    report_lines.append("📈 总计")
    report_lines.append(f"  运行: {total_runs} 次")
    report_lines.append(f"  ✅ 成功: {total_success} 次")
    if total_failure > 0:
        report_lines.append(f"  ❌ 失败: {total_failure} 次")
    
    return "\n".join(report_lines)


def send_to_feishu(webhook_url, report_text):
    """发送报告到飞书 webhook"""
    import urllib.request
    
    payload = {
        "msg_type": "text",
        "content": {
            "text": report_text
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={
            'Content-Type': 'application/json'
        }
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                print("✅ 报告已发送到飞书")
                return True
            else:
                print(f"❌ 飞书发送失败: {result}", file=sys.stderr)
                return False
    except urllib.error.HTTPError as e:
        print(f"❌ 飞书发送失败: HTTP {e.code}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"❌ 飞书发送异常: {type(e).__name__}", file=sys.stderr)
        return False


def main():
    repo_owner = os.environ.get("REPO_OWNER")
    repo_name = os.environ.get("REPO_NAME")
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL")
    
    if not repo_owner or not repo_name:
        print("Error: REPO_OWNER and REPO_NAME must be set", file=sys.stderr)
        sys.exit(1)
    
    if not webhook_url:
        print("Error: FEISHU_WEBHOOK_URL must be set", file=sys.stderr)
        sys.exit(1)
    
    # 计算 24 小时前的时间
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    since_time = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    
    print(f"📊 获取 {since_time} 之后的 workflow runs...")
    runs = get_workflow_runs(repo_owner, repo_name, since_time)
    print(f"   找到 {len(runs)} 条记录")
    
    report = format_report(runs, repo_owner, repo_name)
    print("\n" + report)
    
    print("\n📤 发送到飞书...")
    send_to_feishu(webhook_url, report)


if __name__ == "__main__":
    main()
