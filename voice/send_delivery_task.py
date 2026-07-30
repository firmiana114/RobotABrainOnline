#!/usr/bin/env python3
import argparse
import json
import logging
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_BASE_URL = "http://172.16.2.234:12322"
DEFAULT_TEMPLATE_ID = "delivery_1780402103401"
LOGGER = logging.getLogger("send_delivery_task")


def configure_logging(level_name):
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def http_json(method, url, payload=None, timeout=10):
    body = None
    headers = {"Accept": "application/json"}
    payload_keys = []
    if payload is not None:
        payload_keys = sorted(str(key) for key in payload.keys()) if isinstance(payload, dict) else [type(payload).__name__]
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    start_time = time.perf_counter()
    LOGGER.info(
        "咖啡车接口请求开始: method=%s, url=%s, timeout=%.3fs, payload_keys=%s",
        method,
        url,
        timeout,
        payload_keys,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            elapsed_seconds = time.perf_counter() - start_time
            LOGGER.info(
                "咖啡车接口请求完成: method=%s, url=%s, http_status=%s, bytes=%s, elapsed=%.3fs",
                method,
                url,
                resp.status,
                len(raw.encode("utf-8")),
                elapsed_seconds,
            )
            try:
                return resp.status, json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                LOGGER.error(
                    "咖啡车接口 JSON 解析失败: method=%s, url=%s, http_status=%s, error=%s",
                    method,
                    url,
                    resp.status,
                    exc,
                )
                raise
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        elapsed_seconds = time.perf_counter() - start_time
        LOGGER.warning(
            "咖啡车接口返回 HTTP 错误: method=%s, url=%s, http_status=%s, bytes=%s, elapsed=%.3fs",
            method,
            url,
            exc.code,
            len(raw.encode("utf-8")),
            elapsed_seconds,
        )
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"success": False, "raw": raw}
        return exc.code, data


def print_json(data):
    print(json.dumps(data, ensure_ascii=False, indent=2))


def normalize_base_url(base_url):
    return base_url.rstrip("/")


def run_template(base_url, template_id, timeout):
    url = f"{normalize_base_url(base_url)}/api/delivery/tasks/{urllib.parse.quote(template_id)}/run"
    LOGGER.info("启动咖啡车配送任务: base_url=%s, template_id=%s", normalize_base_url(base_url), template_id)
    status, data = http_json("POST", url, timeout=timeout)
    print_json(data)
    if status != 200 or not data.get("success", False):
        LOGGER.error(
            "咖啡车配送任务启动失败: http_status=%s, success=%s, message=%s",
            status,
            data.get("success"),
            data.get("message") or data.get("error_code") or data.get("error"),
        )
        return 1
    runtime_task_id = data.get("task_id", "")
    if runtime_task_id:
        print(f"\nruntime_task_id={runtime_task_id}")
    LOGGER.info("咖啡车配送任务已启动: runtime_task_id=%s", runtime_task_id or "未返回")
    return 0


def get_status(base_url, timeout):
    url = f"{normalize_base_url(base_url)}/api/task/status"
    LOGGER.info("查询咖啡车任务状态: base_url=%s", normalize_base_url(base_url))
    status, data = http_json("GET", url, timeout=timeout)
    print_json(data)
    ok = status == 200 and data.get("success", False)
    LOGGER.info(
        "咖啡车任务状态查询结束: http_status=%s, success=%s, waiting_for_confirm=%s, task_id=%s",
        status,
        data.get("success"),
        (data.get("data") or {}).get("waiting_for_confirm") if isinstance(data, dict) else None,
        (data.get("data") or {}).get("task_id") if isinstance(data, dict) else None,
    )
    return 0 if ok else 1


def fetch_status_data(base_url, timeout):
    url = f"{normalize_base_url(base_url)}/api/task/status"
    status, data = http_json("GET", url, timeout=timeout)
    if status != 200 or not data.get("success", False):
        raise RuntimeError(f"查询任务状态失败: http={status}")
    return data.get("data", {}) if isinstance(data, dict) else {}


def confirm_return(base_url, task_id, timeout):
    if not task_id:
        LOGGER.info("未显式传入运行任务 ID，先查询当前咖啡车任务状态")
        status_data = fetch_status_data(base_url, timeout)
        if not status_data.get("waiting_for_confirm"):
            raise RuntimeError("当前任务不是 waiting_for_confirm 状态")
        task_id = status_data.get("task_id", "")
        if not task_id:
            raise RuntimeError("当前任务状态缺少 task_id")

    url = f"{normalize_base_url(base_url)}/api/task/command"
    payload = {"command": "confirm", "task_id": task_id}
    LOGGER.info("确认咖啡车任务完成并返航: task_id=%s", task_id)
    status, data = http_json("POST", url, payload=payload, timeout=timeout)
    print_json(data)
    if status == 200 and data.get("success", False):
        print(f"\nconfirmed_task_id={task_id}")
        LOGGER.info("咖啡车返航确认已发送: task_id=%s", task_id)
        return 0
    LOGGER.error(
        "咖啡车返航确认失败: task_id=%s, http_status=%s, success=%s, message=%s",
        task_id,
        status,
        data.get("success"),
        data.get("message") or data.get("error_code") or data.get("error"),
    )
    return 1


def build_parser():
    parser = argparse.ArgumentParser(
        description="调用 AIR 咖啡车配送任务接口，支持启动任务、查询状态和确认返航。"
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"AIR 咖啡车服务地址，默认: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP 请求超时时间，单位秒，默认: 10",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="日志级别，默认: INFO",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="启动已保存的配送任务模板")
    run_parser.add_argument(
        "--template-id",
        default=DEFAULT_TEMPLATE_ID,
        help=f"已保存的配送任务模板 ID，默认: {DEFAULT_TEMPLATE_ID}",
    )

    subparsers.add_parser("status", help="查询当前任务状态")

    confirm_parser = subparsers.add_parser(
        "confirm", help="确认配送完成并让咖啡车返航"
    )
    confirm_parser.add_argument(
        "--task-id",
        default="",
        help="运行时任务 ID；留空时会从 /api/task/status 自动读取",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.log_level)
    try:
        if args.command == "run":
            return run_template(args.base_url, args.template_id, args.timeout)
        if args.command == "status":
            return get_status(args.base_url, args.timeout)
        if args.command == "confirm":
            return confirm_return(args.base_url, args.task_id, args.timeout)
        parser.error(f"不支持的命令: {args.command}")
    except urllib.error.URLError as exc:
        LOGGER.error("咖啡车接口网络错误: error=%s", exc)
        print(f"网络错误: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        LOGGER.error("咖啡车任务处理失败: error=%s", exc)
        print(f"错误: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
