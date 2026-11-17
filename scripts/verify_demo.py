#!/usr/bin/env python3
"""Exercise a running demo through HTTP and decode actual exported images."""

import argparse
import io
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class SmokeError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeError(message)


class Verification:
    def __init__(self, base_url, expected_mode, output_directory):
        self.base_url = base_url
        self.expected_mode = expected_mode
        self.directory = output_directory
        self.report = {
            "startedAt": datetime.now(UTC).isoformat(), "baseUrl": base_url,
            "expectedMode": expected_mode, "status": "running", "checks": [], "artifacts": [],
        }

    def passed(self, identifier, description, **details):
        self.report["checks"].append({"id": identifier, "status": "passed",
                                      "description": description, **details})
        print(f"[通过] {description}")

    def request(self, client, method, path, expected=200, **kwargs):
        response = client.request(method, path, **kwargs)
        require(response.status_code == expected,
                f"{method} {path} 预期HTTP {expected}，实际HTTP {response.status_code}")
        return response

    def create_session(self, client):
        response = self.request(client, "POST", "/api/session")
        require("httponly" in response.headers.get("set-cookie", "").lower(), "会话Cookie缺少HttpOnly")
        require(bool(client.cookies.get("ycy_session")), "未建立会话Cookie")

    def wait_job(self, client, job):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            result = self.request(client, "GET", f"/api/jobs/{job['id']}").json()
            if result["status"] == "succeeded":
                return result
            if result["status"] in {"failed", "superseded"}:
                error_code = (result.get("error") or {}).get("code", result["status"])
                raise SmokeError(f"任务 {job['kind']} 未成功：{error_code}")
            time.sleep(0.25)
        raise SmokeError("等待任务超过150秒；服务端结果未被判定为成功")

    def get_task(self, client, identifier):
        return self.request(client, "GET", f"/api/tasks/{identifier}").json()

    def analyze(self, client, task):
        path = f"/api/tasks/{task['id']}/analyze"
        key = str(uuid4())
        arguments = {"headers": {"Idempotency-Key": key},
                     "json": {"expectedRevision": task["revision"]}}
        job = self.request(client, "POST", path, expected=202, **arguments).json()
        duplicate = self.request(client, "POST", path, expected=202, **arguments).json()
        require(job["id"] == duplicate["id"], "同幂等键产生了不同任务")
        self.wait_job(client, job)
        current = self.get_task(client, task["id"])
        require(current["revision"] == task["revision"] + 1, "分析未正确递增草稿版本")
        require(current["analysisMode"] == self.expected_mode, "分析模式与运行模式不一致")
        return current, job

    def select_template(self, client, task, template):
        return self.request(client, "PATCH", f"/api/tasks/{task['id']}", json={
            "expectedRevision": task["revision"], "templateId": template["id"],
        }).json()

    def confirm_and_render(self, client, task, label, template):
        require(task["phase"] == "needs_confirmation", "信息完整时未进入事实确认阶段")
        confirmed = self.request(client, "POST", f"/api/tasks/{task['id']}/confirm", json={
            "expectedRevision": task["revision"],
            "confirmedFactIds": [fact["id"] for fact in task["analysis"]["facts"]],
        }).json()
        require(confirmed["confirmedRevision"] == task["revision"], "事实确认没有绑定当前版本")
        job = self.request(client, "POST", f"/api/tasks/{task['id']}/render", expected=202,
                           headers={"Idempotency-Key": str(uuid4())},
                           json={"expectedRevision": task["revision"]}).json()
        self.wait_job(client, job)
        rendered = self.get_task(client, task["id"])
        require(rendered["phase"] == "preview_ready" and rendered["previewAssetId"], "缺少成功预览")
        require(rendered["previewRevision"] == rendered["revision"], "预览版本与草稿不一致")
        for extension, mime, image_format in (("png", "image/png", "PNG"), ("jpg", "image/jpeg", "JPEG")):
            response = self.request(client, "GET", f"/api/tasks/{task['id']}/export",
                                    params={"expectedRevision": rendered["revision"], "format": extension})
            require(response.headers.get("content-type", "").startswith(mime), "导出MIME类型错误")
            require("attachment" in response.headers.get("content-disposition", ""), "导出缺少附件响应头")
            with Image.open(io.BytesIO(response.content)) as image:
                image.load()
                size = tuple(image.size)
                require(image.format == image_format, "导出不是可解码的真实图片")
                require(size == (template["canvas"]["width"], template["canvas"]["height"]), "导出尺寸与模板不符")
                if image_format == "JPEG":
                    require(image.mode == "RGB", "JPG未按RGB导出")
            target = self.directory / f"{label}.{extension}"
            target.write_bytes(response.content)
            self.report["artifacts"].append({"path": str(target), "bytes": len(response.content),
                                             "format": image_format, "size": size})
        return rendered, job

    def run(self):
        client_options = {"base_url": self.base_url, "timeout": 30,
                          "headers": {"Origin": self.base_url}, "follow_redirects": False,
                          "trust_env": False}
        with httpx.Client(**client_options) as client, httpx.Client(**client_options) as other:
            health = self.request(client, "GET", "/api/health").json()
            require(health["status"] == "ok", "服务未就绪")
            require(health["mode"] == self.expected_mode,
                    "运行模式不符合预期；为避免误用真实模型，验证在分析前停止")
            self.report["health"] = health
            self.passed("health", "健康接口与显式运行模式一致")
            self.request(client, "GET", "/api/tasks", expected=401)
            self.create_session(client)
            self.create_session(other)
            templates = self.request(client, "GET", "/api/templates").json()["items"]
            template_id = next((item["id"] for item in templates
                                if item["scenario"] == "poster" and item["style"] == "fresh"), None)
            require(template_id is not None, "缺少清新海报演示模板")
            template = self.request(client, "GET", f"/api/templates/{template_id}").json()
            self.passed("session_catalog", "匿名会话与真实模板目录可用，未登录任务接口返回401")

            buffer = io.BytesIO()
            Image.new("RGBA", (280, 160), (112, 145, 90, 180)).save(buffer, "PNG")
            uploaded = self.request(client, "POST", "/api/assets", expected=201,
                                    files={"file": ("demo.png", buffer.getvalue(), "image/png")}).json()
            require((uploaded["width"], uploaded["height"]) == (280, 160), "上传未返回实际解码尺寸")
            self.request(client, "POST", "/api/assets", expected=415,
                         files={"file": ("fake.png", b"<svg></svg>", "image/png")})
            self.request(client, "POST", "/api/assets", expected=415,
                         files={"file": ("fake.jpg", buffer.getvalue(), "image/jpeg")})
            self.passed("upload", "合法透明PNG可上传，SVG伪装与扩展名不匹配被拒绝")

            sample = json.loads((ROOT / "examples/campus-event-complete.json").read_text(encoding="utf-8"))
            sample["assetIds"] = [uploaded["id"]]
            task = self.request(client, "POST", "/api/tasks", expected=201, json=sample).json()
            task = self.select_template(client, task, template)
            task, analysis_job = self.analyze(client, task)
            require(task["analysis"]["missingFields"] == [], "完整样例被误判为信息缺失")
            self.request(client, "POST", f"/api/tasks/{task['id']}/render", expected=409,
                         headers={"Idempotency-Key": str(uuid4())}, json={"expectedRevision": task["revision"]})
            self.request(client, "POST", f"/api/tasks/{task['id']}/confirm", expected=409,
                         json={"expectedRevision": task["revision"], "confirmedFactIds": []})
            rendered, render_job = self.confirm_and_render(client, task, "complete", template)
            self.passed("complete_render", "完整样例经事实确认后生成真实PNG/JPG，尺寸与模板一致",
                        taskId=task["id"], analysisJobId=analysis_job["id"], renderJobId=render_job["id"])
            restored = self.get_task(client, task["id"])
            require(restored["previewAssetId"] == rendered["previewAssetId"], "重新GET未恢复当前预览")
            history = self.request(client, "GET", "/api/tasks").json()["items"]
            require(any(item["id"] == task["id"] for item in history), "任务未进入当前会话历史")
            for path in (f"/api/tasks/{task['id']}", f"/api/jobs/{render_job['id']}",
                         f"/api/assets/{uploaded['id']}/file", f"/api/assets/{rendered['previewAssetId']}/file"):
                self.request(other, "GET", path, expected=404)
            self.passed("restore_isolation", "重新读取恢复任务与预览，其他会话的task/job/asset访问均404")

            fields = dict(restored["fields"], title=restored["fields"]["title"] + "（修改）")
            changed = self.request(client, "PATCH", f"/api/tasks/{task['id']}", json={
                "expectedRevision": restored["revision"], "fields": fields,
            }).json()
            require(changed["confirmedRevision"] is None and changed["previewAssetId"] is None,
                    "修改后旧确认或预览未失效")
            self.request(client, "GET", f"/api/tasks/{task['id']}/export", expected=409,
                         params={"expectedRevision": restored["revision"], "format": "png"})
            self.request(client, "GET", f"/api/tasks/{task['id']}/export", expected=409,
                         params={"expectedRevision": changed["revision"], "format": "png"})
            self.request(client, "PATCH", f"/api/tasks/{task['id']}", expected=409,
                         json={"expectedRevision": restored["revision"], "rawText": "旧版本不能覆盖新版本"})
            self.request(client, "PATCH", f"/api/tasks/{task['id']}", expected=422,
                         json={"expectedRevision": changed["revision"], "ownerSessionId": "forbidden"})
            self.passed("revision", "修改清除旧确认与预览，过期下载/旧版本保存返回409，受控字段拒绝写入")

            sample = json.loads((ROOT / "examples/campus-event-missing.json").read_text(encoding="utf-8"))
            missing = self.request(client, "POST", "/api/tasks", expected=201, json=sample).json()
            missing = self.select_template(client, missing, template)
            missing, _ = self.analyze(client, missing)
            require(set(missing["analysis"]["missingFields"]) == {"time", "location"}, "缺失样例未准确追问时间地点")
            require(missing["fields"]["time"] == missing["fields"]["location"] == "", "缺失关键事实被编造")
            self.request(client, "POST", f"/api/tasks/{missing['id']}/confirm", expected=422,
                         json={"expectedRevision": missing["revision"], "confirmedFactIds": []})
            fields = dict(missing["fields"], time="2026年11月7日14:00", location="图书馆一楼交流区")
            missing = self.request(client, "PATCH", f"/api/tasks/{missing['id']}", json={
                "expectedRevision": missing["revision"], "fields": fields,
            }).json()
            missing, _ = self.analyze(client, missing)
            require(missing["fields"]["time"] == fields["time"] and missing["fields"]["location"] == fields["location"],
                    "用户补充事实被原文覆盖")
            fixed, _ = self.confirm_and_render(client, missing, "missing-fixed", template)
            self.passed("missing_render", "缺失样例先阻止确认，补齐时间地点后生成无图PNG/JPG", taskId=fixed["id"])
            self.report["status"] = "passed"


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="验证已启动的本地演示；默认要求Mock，避免误调用真实模型")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--expect-mode", choices=("mock", "real"), default="mock")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    parsed = urlsplit(args.base_url)
    require(parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username
            and not parsed.password and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment,
            "base-url必须是无凭据、无路径的HTTP/HTTPS源")
    target = args.output_dir or ROOT / "tmp/sprint2-smoke" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=False)
    verification = Verification(args.base_url.rstrip("/"), args.expect_mode, target)
    try:
        verification.run()
    except (SmokeError, httpx.HTTPError, ValueError, KeyError, OSError) as exc:
        verification.report["status"] = "failed"
        # These errors contain paths/status codes, never request headers or session cookies.
        message = str(exc) if not isinstance(exc, httpx.HTTPError) else "HTTP连接或传输失败，请确认服务地址和运行状态"
        verification.report["failure"] = message
        print(f"[失败] {message}", file=sys.stderr)
    finally:
        verification.report["finishedAt"] = datetime.now(UTC).isoformat()
        (target / "summary.json").write_text(json.dumps(verification.report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"验证记录：{target / 'summary.json'}")
    return 0 if verification.report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
