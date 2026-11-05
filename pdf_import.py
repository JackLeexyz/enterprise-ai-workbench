from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess
import sys

from document_import import ImportProblem, MAX_FILE_BYTES, MAX_TEXT_CHARS, reject

MAX_PAGES = 100
MAX_PAGE_STREAM_BYTES = 6 * 1024 * 1024
MAX_TOTAL_STREAM_BYTES = 12 * 1024 * 1024


def parse_pdf(content):
    try:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker"], input=content, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15, check=False)
    except subprocess.TimeoutExpired:
        reject("PDF_TIMEOUT", "PDF 解析超过 15 秒保护时间，请拆分或另存为文本后重试。")
    except OSError:
        reject("PDF_RESOURCE_LIMIT", "PDF 解析进程未能启动，请联系管理员检查运行环境。")
    if completed.returncode or len(completed.stdout) > 512 * 1024:
        reject("PDF_RESOURCE_LIMIT", "PDF 解析进程异常或触发资源保护，请拆分文件后重试。")
    try:
        result = json.loads(completed.stdout)
        if "error" in result:
            reject(result["error"]["code"], result["error"]["message"])
        if not isinstance(result.get("sections"), list) or not result["sections"]:
            raise ValueError()
        return result
    except (ValueError, TypeError, KeyError):
        reject("INVALID_PDF", "PDF 解析结果无效，请另存为普通文本 PDF 后重试。")


def extract_pdf(content):
    from pypdf import PdfReader

    if not content.startswith(b"%PDF-"):
        reject("INVALID_PDF", "文件内容与 PDF 格式不匹配。")
    try:
        reader = PdfReader(io.BytesIO(content), strict=True, root_object_recovery_limit=1000)
        if reader.is_encrypted:
            reject("PDF_ENCRYPTED", "不接受加密或密码保护的 PDF，请提供已授权的普通文本副本。")
        root = reader.trailer["/Root"]
        names = root.get("/Names", {})
        names = names.get_object() if hasattr(names, "get_object") else names
        if any(key in root for key in ("/OpenAction", "/AA", "/AcroForm", "/AF")) or any(key in names for key in ("/JavaScript", "/EmbeddedFiles")):
            reject("UNSAFE_PDF", "不接受带交互动作、表单脚本或嵌入附件的 PDF。")
        if not 1 <= len(reader.pages) <= MAX_PAGES:
            reject("PDF_PAGE_LIMIT", "文本 PDF 需为 1 至 100 页，请拆分后上传。")
        sections, empty_pages, characters, stream_bytes = [], [], 0, 0
        for number, page in enumerate(reader.pages, 1):
            if "/AA" in page or "/AF" in page:
                reject("UNSAFE_PDF", "PDF 页面包含不支持的交互动作或附件。")
            annotations = page.get("/Annots", [])
            annotations = annotations.get_object() if hasattr(annotations, "get_object") else annotations
            if len(annotations) > 1000:
                reject("PDF_RESOURCE_LIMIT", "PDF 页面注释结构过多，请另存为普通文本 PDF。")
            for ref in annotations:
                annotation = ref.get_object()
                if any(key in annotation for key in ("/A", "/AA", "/FS", "/RichMediaContent")):
                    reject("UNSAFE_PDF", "PDF 页面包含交互动作或嵌入对象，请移除后上传。")
            contents = page.get_contents()
            size = len(contents.get_data()) if contents else 0
            stream_bytes += size
            if size > MAX_PAGE_STREAM_BYTES or stream_bytes > MAX_TOTAL_STREAM_BYTES:
                reject("PDF_RESOURCE_LIMIT", "PDF 页面展开内容超过演示保护上限，请拆分文件。")
            body = page.extract_text() or ""
            body = "\n".join(line.strip() for line in body.replace("\x00", "").splitlines() if line.strip())
            characters += len(body)
            if characters > MAX_TEXT_CHARS:
                reject("TEXT_TOO_LARGE", "解析文字超过 40000 字，请拆分文件后上传。")
            if not body:
                empty_pages.append(number)
                continue
            for offset in range(0, len(body), 3500):
                fragment = offset // 3500 + 1
                sections.append({"title": f"第{number}页 片段{fragment}", "text": body[offset:offset + 3500], "page_number": number, "fragment_number": fragment})
            if len(sections) > 120:
                reject("TEXT_TOO_LARGE", "内容结构超过 120 段，请拆分文件后上传。")
        if not sections:
            reject("PDF_NO_TEXT", "PDF 未提取到文字，可能是扫描件；本版不做 OCR，请提供可复制文字的文件。")
        warnings = ["按 PDF 实际物理页提取文字，不保留原样版式、表格结构或精确坐标；页内文字顺序需人工核对。", "图片未做 OCR，图片中的内容未入库。"]
        if empty_pages:
            warnings.append("第 " + "、".join(map(str, empty_pages)) + " 页未提取到文字，未生成引用；请核对空白页或扫描内容。")
        return {"sections": sections, "warnings": warnings, "characters": characters}
    except ImportProblem:
        raise
    except Exception:
        reject("INVALID_PDF", "PDF 已损坏或结构不受支持，请另存为普通文本 PDF 后重试。")


def worker():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (10, 10))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    if sys.platform.startswith("linux"):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    try:
        content = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            reject("FILE_TOO_LARGE", "单文件不能超过 2 MiB。")
        result = extract_pdf(content)
    except ImportProblem as error:
        result = {"error": {"code": error.code, "message": error.message}}
    except ImportError:
        result = {"error": {"code": "PDF_UNAVAILABLE", "message": "缺少 PDF 解析依赖，请管理员安装本版本 requirements.txt 后重试。"}}
    except Exception:
        result = {"error": {"code": "INVALID_PDF", "message": "PDF 解析未完成，请拆分或另存为文本后重试。"}}
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    worker()
